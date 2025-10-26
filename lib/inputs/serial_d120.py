#!/usr/bin/env python
# Serial input source
# Dynon D120/D180 EMS ASCII stream (per Dynon FlightDEK-D180 Appendix A)
# 2025-10-13

from ._input import Input
from lib import hud_utils
import serial
import time
import traceback

from lib.common.dataship.dataship import Dataship
from lib.common.dataship.dataship_engine_fuel import EngineData, FuelData

class serial_d120(Input):
    def __init__(self):
        self.name = "dynon_d120"
        self.version = 1.0
        self.inputtype = "serial"

    def initInput(self, num, dataship: Dataship):
        self.msg_unknown = 0
        self.msg_bad = 0
        Input.initInput(self, num, dataship)  # parent init
        self.output_logBinary = False  # EMS is ASCII
        self.isPlaybackMode = False

        # Playback or live serial
        if (self.PlayFile is not None) and (self.PlayFile is not False):
            if self.PlayFile is True:
                defaultTo = "dynon_d120_data1.txt"
                self.PlayFile = hud_utils.readConfig(self.name, "playback_file", defaultTo)
            self.ser, self.input_logFileName = Input.openLogFile(self, self.PlayFile, "r")
            self.isPlaybackMode = True
        else:
            self.ems_port = hud_utils.readConfig(self.name, "port", "/dev/ttyS1")
            self.ems_baud = hud_utils.readConfigInt(self.name, "baudrate", 115200)
            self.ser = serial.Serial(
                port=self.ems_port,
                baudrate=self.ems_baud,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=1,
            )

        # Create/register engine object
        self.engineData = EngineData()
        self.engineData.id = "d120_engine"
        self.engineData.name = self.name
        self.engine_index = len(dataship.engineData)
        dataship.engineData.append(self.engineData)

        # Create/register fuel object if not present
        self.fuelData = FuelData()
        self.fuelData.id = "d120_fuel"
        self.fuelData.name = self.name
        self.fuel_index = len(dataship.fuelData)
        dataship.fuelData.append(self.fuelData)

    def closeInput(self, dataship):
        try:
            self.ser.close()
        except Exception:
            pass

    #############################################
    # Function: readMessage
    def readMessage(self, dataship: Dataship):
        if dataship.errorFoundNeedToExit: 
            return dataship
        if self.skipReadInput is True:
            return dataship

        try:
            # Read a single EMS line (ASCII) ending with CR LF
            if self.isPlaybackMode:
                line = self.ser.readline()
                if not line:
                    # loop playback file
                    self.ser.seek(0)
                    return dataship
                raw = line.encode("ascii", errors="ignore")
            else:
                # for serial: use .readline() with timeout
                raw = self.ser.readline()
                if not raw:
                    return dataship

            if not self._ems_checksum_ok(raw):
                self.msg_bad += 1
                if self.isPlaybackMode:
                    time.sleep(0.01)
                return dataship

            s = raw.decode("ascii", errors="ignore").strip()
            parsed = self._parse_ems_line(s)
            if not parsed:
                self.msg_bad += 1
                if self.isPlaybackMode:
                    time.sleep(0.01)
                return dataship

            # Map parsed values to EngineData and FuelData
            eng = self.engineData

            # EngineData commonly supports these fields in TronView:
            # RPM, ManPress, OilTemp, OilPress, FuelPress, FuelFlow, Volts, Amps, EGT, CHT, NumberOfCylinders
            eng.RPM        = parsed.get("rpm", 0)
            eng.ManPress   = round(parsed.get("map_inhg", 0.0), 2)     # inHg
            eng.OilTemp    = round(parsed.get("oil_temp_f", 0.0), 1)   # F
            eng.OilPress   = round(parsed.get("oil_psi", 0.0), 1)      # PSI
            eng.FuelPress  = round(parsed.get("fuel_psi", 0.0), 1)     # PSI
            eng.FuelFlow   = round(parsed.get("ff_gph", 0.0), 2)       # GPH
            # Volts/Amps exist in some EngineData definitions
            if hasattr(eng, "Volts"):
                eng.Volts = round(parsed.get("volts_v", 0.0), 1)
            if hasattr(eng, "Amps"):
                eng.Amps  = parsed.get("amps_a", 0)

            egt = parsed.get("egt_f", [])
            cht = parsed.get("cht_f", [])
            # Ensure arrays exist; EngineData.EGT/CHT likely expect length >= max cylinders
            if hasattr(eng, "EGT") and isinstance(eng.EGT, list):
                for i, v in enumerate(egt):
                    if i < len(eng.EGT):
                        eng.EGT[i] = v
            if hasattr(eng, "CHT") and isinstance(eng.CHT, list):
                for i, v in enumerate(cht):
                    if i < len(eng.CHT):
                        eng.CHT[i] = v
            if hasattr(eng, "NumberOfCylinders"):
                eng.NumberOfCylinders = max(len(egt), len(cht)) if (egt or cht) else getattr(eng, "NumberOfCylinders", 0)

            eng.msg_last  = s
            eng.msg_count += 1

            # Fuel levels -> FuelData.FuelLevels (gallons)
            if hasattr(self.fuelData, "FuelLevels") and isinstance(self.fuelData.FuelLevels, list):
                # ensure FuelLevels has enough slots
                while len(self.fuelData.FuelLevels) < 2:
                    self.fuelData.FuelLevels.append(0.0)
                self.fuelData.FuelLevels[0] = round(parsed.get("fuel_l1_g", 0.0), 1)
                self.fuelData.FuelLevels[1] = round(parsed.get("fuel_l2_g", 0.0), 1)
                # total if FuelData supports it
                if hasattr(self.fuelData, "FuelTotal"):
                    self.fuelData.FuelTotal = round(parsed.get("fuel_remain_g", 0.0), 1)
                self.fuelData.msg_count = getattr(self.fuelData, "msg_count", 0) + 1

            # Optional log
            if self.output_logFile is not None:
                Input.addToLog(self, self.output_logFile, s + '\n')

            if self.isPlaybackMode:
                time.sleep(0.01)

            return dataship

        except serial.serialutil.SerialException:
            print("dynon d120 serial exception")
            traceback.print_exc()
            dataship.errorFoundNeedToExit = True
        except Exception as e:
            print("dynon d120 unexpected error:", e)
            traceback.print_exc()
            dataship.errorFoundNeedToExit = True

        return dataship

    def _ems_checksum_ok(self, raw: bytes) -> bool:
        """Validate self-zeroing checksum.
        Sum(payload bytes) + checksum_byte == 0 (mod 256). Line ends with CR LF."""
        try:
            body = raw.rstrip(b"\r\n")
            if len(body) < 4:
                return False
            chk_hex = body[-2:].decode("ascii")
            chk_val = int(chk_hex, 16)
            payload = body[:-2]
            total = (sum(payload) + chk_val) & 0xFF
            return total == 0
        except Exception:
            return False

    def _parse_ems_line(self, s: str):
        """Parse Dynon D120/D180 EMS fixed-width ASCII line."""
        i = 0
        def take(n):
            nonlocal i
            v = s[i:i+n]
            i += n
            return v

        try:
            # Time (Zulu): hhmmss + 1/64 sec counter
            _z_h = int(take(2)); _z_m = int(take(2)); _z_s = int(take(2)); _frac = int(take(2))
            # MAP inHg x100
            map_raw = int(take(4))

            # Oil temp (signed) and oil press PSI
            oil_temp_str = take(3)
            oil_temp_f = int(oil_temp_str)
            oil_psi = int(take(3))

            # Fuel press (PSI x10), Volts (x10), Amps (signed)
            fuel_psi_tenths = int(take(3))
            volts_tenths    = int(take(3))
            amps_str        = take(3)
            amps_a          = int(amps_str)

            # RPM/10, Fuel flow (GPH x10)
            rpm_div10 = int(take(3))
            ff_tenths = int(take(3))

            # Fuel remaining (g x10), Fuel level tank1 (g x10), tank2 (g x10)
            fuel_remain_tenths = int(take(4))
            fuel_l1_tenths     = int(take(3))
            fuel_l2_tenths     = int(take(3))

            # GP fields (skip, 8 chars each)
            _gp1 = take(8)
            _gp2 = take(8)
            _gp3 = take(8)

            # GP thermocouple (4)
            _gp_tc = take(4)

            # EGT 6×4 chars (F, signed)
            egt = [int(take(4)) for _ in range(6)]
            # CHT 6×3 chars (F, signed)
            cht = [int(take(3)) for _ in range(6)]

            # Contacts
            contact1 = int(take(1))
            contact2 = int(take(1))
            _prod_id = take(2)
            # checksum was validated earlier

            # Convert units
            map_inhg  = map_raw / 100.0
            fuel_psi  = fuel_psi_tenths / 10.0
            volts_v   = volts_tenths / 10.0
            rpm       = rpm_div10 * 10
            ff_gph    = ff_tenths / 10.0
            fuel_rem_g= fuel_remain_tenths / 10.0
            fuel_l1_g = fuel_l1_tenths / 10.0
            fuel_l2_g = fuel_l2_tenths / 10.0

            return {
                "map_inhg": map_inhg,
                "oil_temp_f": oil_temp_f,
                "oil_psi": oil_psi,
                "fuel_psi": fuel_psi,
                "volts_v": volts_v,
                "amps_a": amps_a,
                "rpm": rpm,
                "ff_gph": ff_gph,
                "fuel_remain_g": fuel_rem_g,
                "fuel_l1_g": fuel_l1_g,
                "fuel_l2_g": fuel_l2_g,
                "egt_f": egt,
                "cht_f": cht,
                "contact1": contact1,
                "contact2": contact2
            }
        except Exception:
            return None

# vi: modeline tabstop=8 expandtab shiftwidth=4 softtabstop=4 syntax=python