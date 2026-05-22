import serial
import time
import logging

# --- VERSIONING ---
__version__ = "1.0.0"

# --- CUSTOM EXCEPTIONS ---
class TertiumError(Exception):
    """Base exception for Tertium Reader"""
    pass

class TertiumConnectionError(TertiumError):
    """Raised when serial connection fails"""
    pass

class TertiumProtocolError(TertiumError):
    """Raised when the reader returns an error code"""
    pass

class TertiumReader:
    # --- PROTOCOL CONSTANTS ---
    CMD_BEEPER = "01"
    CMD_LED = "02"
    CMD_MODE = "0D"        # Volatile mode (current session only)
    CMD_SET_MODE = "0E"    # Persistent mode (saves to EEPROM)
    CMD_SET_STANDARD = "0F"
    CMD_INVENTORY = "11"
    CMD_WRITE_ID = "12"
    CMD_READ_BANK = "13"
    CMD_WRITE_BANK = "14"
    CMD_READ_TEMP = "1B"
    CMD_SET_RSSI_FILTER = "1C" # RE-40 Specific
    CMD_SET_ID_FILTER = "1D"   # RE-40 Specific
    CMD_SET_POWER = "1F"
    
    
    RET_SUCCESS = "00"
    RET_TIMEOUT = "0D"

    def __init__(self, port, baudrate=38400, timeout=1, rssi_enabled=False, logger=None):
        """
        Initializes the reader. Accepts an optional logger instance.
        """
        """
        Initializes the serial connection with the Tertium RFID reader.
        
        Standard port configuration:
        - Baudrate: 38400 (default for most devices)
        - Byte size: 8 bit
        - Parity: None
        - Stop bits: 1
        - Flow control: None (No hardware or software flow control)
        
        Args:
            port (str): The COM port (e.g., 'COM3' on Windows or '/dev/ttyUSB0' on Linux).
            baudrate (int): Transmission speed (default 38400).
            timeout (int): Read timeout in seconds for blocking operations.
            logger: Accepts an optional logger instance
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.logger = logger or logging.getLogger(__name__)
        if rssi_enabled==True: # Tracks if RSSI value is expected in inventory responses
            self.rssi_enabled = True 
        else:
            self.rssi_enabled = False

    def open(self):
        """Opens the serial port and waits for initialization."""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            self.logger.info(f"Connected to {self.port} at {self.baudrate} bps")
            self.ser.reset_input_buffer()
            time.sleep(2.0) # Hardware boot wait
        except serial.SerialException as e:
            raise TertiumConnectionError(f"Could not open port {self.port}: {e}")

    def close(self):
        """Safely closes the serial port."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.logger.info("Serial port closed.")

    # Context Manager support
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @staticmethod
    def _calculate_frame(cmd_code, params="", seq="00"):
        """
        Builds the command frame according to protocol specifications.
        
        Frame structure:
        $: + Length(2) + Sequence(2) + CmdCode(2) + Params(n) + CR
        
        Notes:
        - Length is calculated in hexadecimal bytes (excluding $: header and terminator).
        - \r (CR) is used as the universal terminator.
        
        Args:
            cmd_code (str): Hexadecimal command code (e.g., "11").
            params (str): Hexadecimal parameter string.
            seq (str): Sequence number (default "00").

        Returns:
            bytes: The ASCII encoded frame ready for transmission.
        """
        raw_len = 6 + len(params)
        #return f"$:{raw_len:02X}{seq}{cmd_code}{params}\r".encode('ascii')
        frame = f"$:{raw_len:02X}{seq}{cmd_code}{params}\r"
        return frame.encode('ascii')
    
    def send_command(self, cmd_code, params=""):
        """
        Handles command sending and raw response reception.
        
        Args:
            cmd_code (str): Command code (e.g., "01", "0E").
            params (str): Command parameters.

        Returns:
            str: Decoded response (without terminators) or None in case of error.
        """
        if not self.ser or not self.ser.is_open:
            raise TertiumConnectionError("Port is not open.")

        
        frame = self._calculate_frame(cmd_code, params)
        self.ser.write(frame) # Scrive il comando sul reader
        
        #Temp
        if cmd_code == self.CMD_READ_TEMP:
            print(f"Read Temperature Command Frame: {frame}")
        
        #Risposta del reader
        raw_resp = self.ser.read_until(b'\r').decode('ascii').strip()
        
        if cmd_code == self.CMD_READ_TEMP:
            print(f"Read Temperature Command Response: {raw_resp}")
        #-------------------------------------------------------------
        
        if not raw_resp:
            self.logger.error(f"Timeout waiting for command {cmd_code}")
            return None
        
        return raw_resp

    # --- DIAGNOSTIC & HARDWARE COMMANDS ---

    def get_status(self):
        """
        Verifies reader status and firmware version.
        Used as a 'ping' to verify the reader is responding.
        
        Returns:
            bool: True if the reader communicates and returns code 00.
        """
        resp = self.send_command(self.CMD_SET_STANDARD, "FD")
        if not resp:
            return False
            
        if resp.startswith("$:") and len(resp) >= 10:
            ret_code = resp[6:8]
            if ret_code == self.RET_SUCCESS:
                self.logger.info(f"Reader ready. FW Version: {resp[8:10]}")
                return True
            else:
                self.logger.error(f"Reader returned error code: {ret_code}")
        else:
            self.logger.error(f"Invalid response format: {resp}")
            
        return False

    def software_reset(self):
        """Sends a software reset command to reboot the device."""
        self.logger.info("Sending Software Reset command...")
        if self.ser and self.ser.is_open:
            self.ser.write(self._calculate_frame(self.CMD_SET_STANDARD, "FF"))
            time.sleep(2.0)

    def beep(self, freq_hz=1000, duration_ms=200):
        """
        Activates the beeper.
        
        Args:
            freq_hz (int): Frequency in Hz (Valid range: 40Hz - 20000Hz).
            duration_ms (int): Duration of the sound in milliseconds.

        Returns:
            bool: True if the command is accepted by the reader.
        """
        if not (40 <= freq_hz <= 20000): freq_hz = 1000
        params = f"{int(freq_hz):04X}{int(duration_ms/10):02X}0001003200"
        resp = self.send_command(self.CMD_BEEPER, params)
        return resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS

    def set_led(self, red_status="00", green_status="00", color=None):
        """
        Controls the reader LEDs (Command 02).
        
        Args:
            red_status (str): Red LED Status (00=OFF, FF=ON, intermediate=Blinking).
            green_status (str): Green LED Status (00=OFF, FF=ON, intermediate=Blinking).
            color (str): (Optional) Color code for RGB models (e.g., RE40 Scanner).
                         01=Red, 02=Green, 04=Blue, 07=White.

        Returns:
            bool: True if operation successful.
        """
        params = f"{red_status.upper().zfill(2)}{green_status.upper().zfill(2)}{f'{int(color):02X}' if color else ''}"
        resp = self.send_command(self.CMD_LED, params)
        return resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS

    # --- CONFIGURATION COMMANDS ---

    def set_power(self, power_val=1, mode="00"):
        """
        Sets the RF transmission power
        
        Args:
            power_val (int): Power or attenuation value.
                             For RE40 modules: 00 = Max Power (27dBm), 1B = Min Power (0dBm).
                             For other modules: 01 = -1dB relative to max.
            mode (str): Amplifier mode (00=Auto, 01=Fixed Low, 02=Fixed High).

        Returns:
            bool: True if the command is accepted.
        """
        params = f"{int(power_val):02X}{mode.zfill(2)}"
        resp = self.send_command(self.CMD_SET_POWER, params)
        if resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS:
            self.logger.info(f"Power set to {27-power_val} dBm (Hex: {params[:2]}). Reset may be required.")
            return True
        return False
    
    def set_current_mode(self, mode="00", local="00", id_format="00", t_scan="05", t_interval="05"):
        """
        Sets the current operation mode (Command 0D). 
        This is VOLATILE: settings are lost when the reader is powered off.
        
        Args:
            mode (str): Scan mode.
                        - "00": Normal (waits for Inventory command from host).
                        - "01": Time-based (automatic cyclic scan).
                        - "02": Input-based (activation via button or sensor).
            local (str): Feedback management (Beeper/LED/Vibration).
                         - "00": Automatic feedback on tag read.
                         - "01": Feedback disabled (managed by host only).
            id_format (str): Response data format.
                             - "00": Full frame ($: + EPC).
                             - "01": EPC only + CR LF.
            t_scan (str): Scan duration timeout (multiples of 100ms, e.g., "05" = 500ms).
            t_interval (str): Pause between scans (multiples of 100ms).
        
        Returns:
            bool: True if configuration was setted.
        """
        params = (f"{str(mode).zfill(2).upper()}{str(local).zfill(2).upper()}"
                  f"{str(id_format).zfill(2).upper()}00{str(t_scan).zfill(2).upper()}"
                  f"{str(t_interval).zfill(2).upper()}")
        resp = self.send_command(self.CMD_MODE, params)
        if resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS:
            self.logger.info("Current operation mode set successfully (Volatile).")
            return True
        return False

    def set_operation_mode(self, mode="00", local="00", id_format="00", t_scan="05", t_interval="05"):
        """
        Configures the operation mode and saves parameters to memory (Command 0E).
        
        Args:
            mode (str): Scan mode.
                        - "00": Normal (waits for Inventory command from host).
                        - "01": Time-based (automatic cyclic scan).
                        - "02": Input-based (activation via button or sensor).
            local (str): Feedback management (Beeper/LED/Vibration).
                         - "00": Automatic feedback on tag read.
                         - "01": Feedback disabled (managed by host only).
            id_format (str): Response data format.
                             - "00": Full frame ($: + EPC).
                             - "01": EPC only + CR LF.
            t_scan (str): Scan duration timeout (multiples of 100ms, e.g., "05" = 500ms).
            t_interval (str): Pause between scans (multiples of 100ms).
        
        Returns:
            bool: True if configuration was saved.
        """
        params = (f"{str(mode).zfill(2).upper()}{str(local).zfill(2).upper()}"
                  f"{str(id_format).zfill(2).upper()}00{str(t_scan).zfill(2).upper()}"
                  f"{str(t_interval).zfill(2).upper()}")
        resp = self.send_command(self.CMD_SET_MODE, params)
        if resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS:
            self.logger.info("Operation mode configured successfully.")
            return True
        else:
            self.logger.error(f"Failed to set operation mode. Response: {resp}")
            return False
        
    def set_rssi_filter(self, enabled=False, threshold_dbm=-80):
        """
        Enables RSSI filtering and presence in inventory output (RE-40 only).
        
        Args:
            enabled (bool): If True, enables RSSI presence and filtering.
            threshold_dbm (int): RSSI threshold in dBm (-128 to -1).
        """
        active = "01" if enabled else "00"
        # Convert dBm to 2's complement hex (80 to FF)
        threshold = max(-128, min(-1, threshold_dbm))
        threshold_hex = f"{ (threshold & 0xFF) :02X}"
        
        params = f"{active}{threshold_hex}"
        resp = self.send_command(self.CMD_SET_RSSI_FILTER, params)
        return resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS

    def set_id_filter(self, filter_type=0, mask1="", mask2=""):
        """
        Filters IDs based on EPC prefix masks (RE-40 only).
        
        Args:
            filter_type (int): 0 (Disabled), 1 (One mask), 2 (Two masks).
            mask1 (str): Hex string for mask 1.
            mask2 (str): Hex string for mask 2.
        """
        f_type_hex = f"{filter_type:02X}"
        
        m1_len_hex = f"{len(mask1)//2:02X}" if mask1 else "00"
        m1_hex = mask1.upper() if mask1 else ""
        
        m2_len_hex = f"{len(mask2)//2:02X}" if mask2 else "00"
        m2_hex = mask2.upper() if mask2 else ""
        
        params = f"{f_type_hex}{m1_len_hex}{m1_hex}{m2_len_hex}{m2_hex}"
        resp = self.send_command(self.CMD_SET_ID_FILTER, params)
        return resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS

    # --- RFID COMMANDS ---

    def inventory(self, timeout_ms=500):
        """
        Performs a synchronous inventory (Command 11).
        Waits for the specified time collecting all unique tags found.
        If RSSI presence is enabled, returns a list of tuples: (EPC, RSSI_dBm).
        
        Args:
            timeout_ms (int): Scan duration in milliseconds.
        
        Returns:
            reads: List of detected EPCs.
        """
        timeout_hex = f"{int(timeout_ms / 100):02X}"
        self.ser.write(self._calculate_frame(self.CMD_INVENTORY, timeout_hex))
        
        reads = []
        start = time.time()
        while (time.time() - start) < (timeout_ms/1000 + 1.0):
            line = self.ser.read_until(b'\r').decode('ascii').strip()
            if not line.startswith("$:"): continue
            if len(line) <= 12 and line[6:8] == self.RET_TIMEOUT: break
            
            if len(line) > 12:
                tag_data = line[10:]
                
                if self.rssi_enabled:
                    # Last 2 characters are the RSSI hex value
                    epc = tag_data[:-2]
                    rssi_hex = tag_data[-2:]
                    # Convert 2's complement hex to signed integer
                    rssi = int(rssi_hex, 16)
                    if rssi > 127: rssi -= 256
                    reads.append((epc, rssi))
                else:
                    reads.append(tag_data)
                    
        return reads

    def read_memory(self, epc, mem_bank="03", address="00", block_num="01", password="", timeout_ms=500):
        """
        Reads data blocks from a specific memory bank of a tag.
        
        Args:
            epc (str): The target tag EPC (hex string).
            mem_bank (str): "00"=RESERVED, "01"=EPC, "02"=TID, "03"=USER.
            address (str): Start address (hex string).
            block_num (str): Number of blocks to read (hex string).
            password (str): Access password if required (8 hex digits).
            timeout_ms (int): Command response timeout in milliseconds.
            
        Returns:
            resp (str): Returns the memory blocks read
        """
        # Convert timeout in milliseconds to protocol hex format (units of 100ms)
        timeout_hex = f"{int(timeout_ms / 100):02X}"
        
        params = f"{timeout_hex}{epc}{mem_bank}{address}{block_num}{password}"
        resp = self.send_command(self.CMD_READ_BANK, params)
        
        if resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS:
            return resp[8:] # Returns the memory blocks read
            
        self.logger.warning(f"Read failed with response: {resp}")
        return None
    
    def write_memory(self, epc, data, mem_bank="03", address="00", block_num="01", timeout_ms=1000, acc_password=""):
        """
        write data in memory blocks of the addressed TAG

        Parameters
        ----------
        timeout_ms : int
            command response timeout
        epc : str
            ID of the addressed tag.
        mem_bank : 2 hex
            hexadecimal digits where the most significant nibble is 0 to execute a Write command of Gen 2 standard, while
            is 1 to execute a BlockWrite (refer to the datasheet of the tag to verify if tag supports BlockWrite command). The less significant nibble is used to
            select the memory bank: 0 = RESERVED, 1 = EPC, 2 = TID, 3 = USER)
        address : 2 hex
            address of the block to be written.
        block_num : 2 hex
            number of blocks to be written (1 block = 4 hexadecimal digits).
        data : str
            data to be entered.
        acc_password : 8 hex
            access password (optional; 8 hexadecimal digits). To use in tag that need access password to go in secure state to read a specific
            memory bank

        Returns
        -------
        retcode : TYPE
            DESCRIPTION.

        """
        # Convert timeout in milliseconds to protocol hex format (units of 100ms)
        timeout_hex = f"{int(timeout_ms / 100):02X}"
        params = f"{timeout_hex}{epc}{mem_bank}{address}{block_num}{data}{acc_password}"
        
        resp = self.send_command(self.CMD_WRITE_BANK, params)
        
        if resp and len(resp) >= 6 and resp[4:6] == self.RET_SUCCESS:
            self.logger.info("Write successfull")
            return resp[4:6]
        
        self.logger.warning(f"Write failed with response: {resp}")
        
        return None

    def read_temperature(self, epc, timeout_ms=1000, tag_type="01", tag_subtype="00", password=""):
        """
        Reads temperature from a sensor tag according to the READTEMP protocol.
        
        Args:
            epc (str): The target tag EPC (hex string). Note: if length != 12 bytes 
                       it must be prefixed by the 4 hex digits PC.
            timeout_ms (int): Command response timeout in milliseconds (default 1000ms).
            tag_type (str): Sensor tag type (default "01" = asYgn AS321x).
            tag_subtype (str): Sensor tag subtype (default "00" = RFU).
            password (str): Access password if required (8 hex digits).
            
        Returns:
            temp_val (float): Temperature in °C, or None if error/invalid.
        """
        # Convert timeout in milliseconds to protocol hex format (units of 100ms)
        timeout_hex = f"{int(timeout_ms / 100):02X}"
        
        # Build parameters: [timeout][tag-type][tag-subtype][EPC][acc password]
        params = f"{timeout_hex}{tag_type}{tag_subtype}{epc}{password}"
        resp = self.send_command(self.CMD_READ_TEMP, params)
                
        if resp and len(resp) >= 8 and resp[6:8] == self.RET_SUCCESS:
            try:
                # Response string structure: 
                # $:LLSS[retcode(2)][type(2)][subtype(2)][validity(2)][temp(4)]
                # Indices:
                # 6:8   -> retcode
                # 8:10  -> tag_type
                # 10:12 -> tag_subtype
                # 12:14 -> validity
                # 14:18 -> temperature
                validity = resp[12:14]
                print(validity)
                temp_val = int(resp[14:18], 16) / 10.0
                
                # Check validity as per manual (00=invalid, 01=valid but not accurate, 02=valid and accurate)
                if validity == "00":
                    self.logger.warning(f"Temperature data is INVALID for tag {epc[-6:]}")
                elif validity == "01":
                    self.logger.info(f"Temperature is valid but NOT ACCURATE for tag {epc[-6:]}")
                
                return temp_val
            except (ValueError, IndexError):
                self.logger.warning(f"Malformed temperature data: {resp}")
        else:
            self.logger.warning(f"Temperature read failed or timed out. Response: {resp}")
        return None
    
    def listen_async(self, callback=None):
        """
        Continuously listens to the serial port for asynchronous data (Scan on Time / Scan on Input).
        
        Args:
            callback (function): Function to call when a tag is detected.
                                 If None, the tag will just be printed in the logs.
        """
        self.logger.info("Listening for asynchronous data... (Press Ctrl+C to stop)")
        try:
            while True:
                if self.ser.in_waiting > 0:
                    try:
                        line = self.ser.read_until(b'\r').decode('ascii').strip()
                        if line.startswith("$:") and len(line) > 12:
                            tag_data = line[10:]
                            
                            if self.rssi_enabled:
                                epc = tag_data[:-2]
                                rssi_hex = tag_data[-2:]
                                rssi = int(rssi_hex, 16)
                                if rssi > 127: rssi -= 256
                                tag_payload = (epc, rssi)
                            else:
                                tag_payload = tag_data
                                
                            if callback: 
                                callback(tag_payload)
                            else: 
                                self.logger.info(f"[ASYNC] {tag_payload}")
                    except Exception as e:
                        self.logger.error(f"Async error: {e}")
                time.sleep(0.01)
                
        except KeyboardInterrupt:
            self.logger.info("Asynchronous listening stopped by the user.")
