-- gip_hotas_merge.lua
-- usb-proxy Lua transform (requires Lua 5.3/5.4 — see setup_opi_b.sh).
--
-- Rewrites GIP input reports (message type 0x20) from a genuine Xbox
-- controller, merging in HOTAS state published by hotas_receiver.py at
-- /dev/shm/hotas_state. Everything else (auth, rumble, other GIP
-- messages) passes through untouched.
--
-- Merge policy:
--   * buttons: OR of pad and HOTAS
--   * sticks/triggers: HOTAS value when its override flag is set, EXCEPT
--     when the pad's own input is clearly active — then the pad wins.
--     So you can grab the gamepad mid-session without touching anything.
--   * failsafe: if the shared record's sequence number stops changing
--     (sender dead / WiFi down), fall back to pure passthrough.
--
-- GIP input report layout (1-indexed as Lua sees it):
--   [1]=0x20 type  [2]=flags  [3]=seq  [4]=payload len
--   [5..6]   buttons u16 LE
--   [7..8]   left trigger  u16 LE (0..1023)
--   [9..10]  right trigger u16 LE
--   [11..12] LX i16 LE   [13..14] LY   [15..16] RX   [17..18] RY

local STATE_FILE      = "/dev/shm/hotas_state"
local STALE_REPORTS   = 150     -- ~1.2 s at the pad's 125 Hz report rate
local PAD_STICK_WIN   = 8000    -- |pad stick| above this and the pad wins
local PAD_TRIG_WIN    = 100     -- pad trigger above this and the pad wins

local FLAG_LEFT, FLAG_RIGHT, FLAG_TRIG = 0x01, 0x02, 0x04

local f = nil
local last_seq, same_count = -1, 0
local s = nil                   -- last good parsed state

local function u16(str, i) return str:byte(i) | (str:byte(i + 1) << 8) end
local function i16(str, i)
    local v = u16(str, i)
    if v >= 0x8000 then v = v - 0x10000 end
    return v
end
local function tbl_i16(data, i)
    local v = data[i] | (data[i + 1] << 8)
    if v >= 0x8000 then v = v - 0x10000 end
    return v
end
local function put_i16(data, i, v)
    if v < -32768 then v = -32768 elseif v > 32767 then v = 32767 end
    if v < 0 then v = v + 0x10000 end
    data[i]     = v & 0xFF
    data[i + 1] = (v >> 8) & 0xFF
end
local function put_u16(data, i, v)
    if v < 0 then v = 0 elseif v > 1023 then v = 1023 end
    data[i]     = v & 0xFF
    data[i + 1] = (v >> 8) & 0xFF
end

local function read_state()
    if not f then
        f = io.open(STATE_FILE, "rb")
        if not f then return end
        f:setvbuf("no")
    end
    f:seek("set", 0)
    local rec = f:read(40)
    if not rec or #rec < 40 or rec:sub(1, 4) ~= "HB01" then return end

    local seq  = u16(rec, 5) | (u16(rec, 7) << 16)
    local seq2 = u16(rec, 25) | (u16(rec, 27) << 16)
    if seq ~= seq2 then return end          -- torn read; keep previous state

    if seq == last_seq then
        same_count = same_count + 1
    else
        same_count = 0
        last_seq = seq
    end

    s = {
        lx = i16(rec, 9),  ly = i16(rec, 11),
        rx = i16(rec, 13), ry = i16(rec, 15),
        lt = u16(rec, 17), rt = u16(rec, 19),
        buttons = u16(rec, 21),
        flags   = rec:byte(23),
    }
end

function transform(data, len)
    -- Only touch full GIP input reports
    if len < 18 or data[1] ~= 0x20 then
        return data, len
    end

    read_state()
    if not s or same_count > STALE_REPORTS then
        return data, len                    -- failsafe: pure passthrough
    end

    -- Buttons: OR the HOTAS layer on top of the pad
    data[5] = data[5] | (s.buttons & 0xFF)
    data[6] = data[6] | ((s.buttons >> 8) & 0xFF)

    -- Left stick
    if (s.flags & FLAG_LEFT) ~= 0 then
        local px, py = tbl_i16(data, 11), tbl_i16(data, 13)
        if math.abs(px) < PAD_STICK_WIN and math.abs(py) < PAD_STICK_WIN then
            put_i16(data, 11, s.lx)
            put_i16(data, 13, s.ly)
        end
    end

    -- Right stick
    if (s.flags & FLAG_RIGHT) ~= 0 then
        local px, py = tbl_i16(data, 15), tbl_i16(data, 17)
        if math.abs(px) < PAD_STICK_WIN and math.abs(py) < PAD_STICK_WIN then
            put_i16(data, 15, s.rx)
            put_i16(data, 17, s.ry)
        end
    end

    -- Triggers
    if (s.flags & FLAG_TRIG) ~= 0 then
        if (data[7] | (data[8] << 8)) < PAD_TRIG_WIN then
            put_u16(data, 7, s.lt)
        end
        if (data[9] | (data[10] << 8)) < PAD_TRIG_WIN then
            put_u16(data, 9, s.rt)
        end
    end

    return data, len
end
