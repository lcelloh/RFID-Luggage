# -*- coding: utf-8 -*-
"""
Created on Thu May 21 10:32:38 2026

@author: aless
"""

import logging
import time
import argparse
import webbrowser

# Import the class and exceptions from your tertium_serial_handler.py file
from tertium_serial_handler import TertiumReader, TertiumError

def main(power, rssi):
    
    # Configure logging to see timestamps and levels clearly
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(levelname)s: %(message)s', 
        datefmt='%H:%M:%S'
    )
    logger = logging.getLogger("SyncInventory")
    
    # Serial Port configuration (modify as needed for your OS)
    PORT = 'COM3'
    
    try:
        # Use the Context Manager to ensure safe port opening and closing
        with TertiumReader(port=PORT, rssi_enabled=rssi) as reader:
            
            # --- STARTUP CLEANUP ---
            # Ensure the reader is not flooding the buffer from a previous async session
            logger.info("Initializing reader and clearing buffers...")
            if reader.ser:
                reader.ser.reset_input_buffer()
                time.sleep(0.1)
                
                # Force Mode 00 (Synchronous/Normal mode)
                reader.set_led(red_status="FF") # Visual feedback: Red light during init
                reader.set_operation_mode(mode="00") 
                
                # Set reader params
                reader.set_power(power_val=power)
                
                
                time.sleep(0.1)
                reader.ser.reset_input_buffer()

            # Verify reader connectivity (Ping)
            if not reader.get_status():
                logger.error("Reader not responding. Check connection and port.")
                return

            logger.info("Reader ready in Synchronous Mode.")
            reader.set_led(green_status="FF", red_status="00") # Solid green: System ready
            reader.beep(freq_hz=1000, duration_ms=200)
            
            # --- SYNCHRONOUS INVENTORY LOOP ---
            timeout_ms=2000
            k=0
            try:
                while True:
                    logger.info(f"--- Starting {k}th Scan Cycle ({timeout_ms/1000} s) ---")
                    
                    # Request a synchronous inventory
                    # This call blocks the script until the reader's internal timeout expires
                    
                    tags = reader.inventory(timeout_ms=timeout_ms)
                    
                    if not tags:
                        logger.info("No tags found in the RF field.")
                    else:
                        # If rssi_enabled is True in the class, 'tags' will be a list of tuples (epc, rssi)
                        # Otherwise, it will be a list of strings (epc)
                        logger.info(f"Found {len(tags)} tag(s):")
                        reader.beep(freq_hz=2000, duration_ms=100) # Audio feedback for detection
                        
                        for entry in tags:
                            if isinstance(entry, tuple):
                                epc, rssi = entry
                                logger.info(f" >>> Tag Detected: {epc} | RSSI: {rssi} dBm")
                            else:
                                epc = entry
                                logger.info(f" >>> Tag Detected: {epc}")
                                
                                bits = bin(int(epc, 16))[2:].zfill(96)
                                GTIN=bits[14:58]
                                SN=bits[58:96]
                                
                                GTIN_hex = str(int(GTIN, 2)).zfill(14)
                                SN_hex = str(int(SN, 2))
                                
                                url=f"https://alessiomos97.github.io/RFIDLab2026/01/{GTIN_hex}/21/{SN_hex}"
                                webbrowser.open(url)  # Go to example.com
                                    
                    print("-" * 45)
                    time.sleep(1.0) # Pause between scan cycles to avoid hardware saturation
                    k+=1
                    
            except KeyboardInterrupt:
                logger.info("Scan loop interrupted by user (Ctrl+C).")

            finally:
                # Reset UI state (turn off LEDs)
                logger.info("Resetting reader status...")
                try:
                    reader.set_led(green_status="00", red_status="00")
                except:
                    pass

    except TertiumError as e:
        logger.error(f"Tertium Hardware Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    
    # Add argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("--power", type=float, default=0)
    parser.add_argument("--rssi", type=bool, default=False)

    args = parser.parse_args()
    
    #Add parsed argument to main
    main(power=args.power, rssi=args.rssi)