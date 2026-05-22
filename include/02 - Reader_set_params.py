# -*- coding: utf-8 -*-
"""
Created on Thu May 21 10:32:38 2026

@author: aless
"""

import logging
import time
import argparse

# Import the class and exceptions from your tertium_serial_handler.py file
from tertium_serial_handler import TertiumReader, TertiumError

def main(power):
    
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
        with TertiumReader(port=PORT) as reader:
            
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


    except TertiumError as e:
        logger.error(f"Tertium Hardware Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    
    # Add argument parser
    parser = argparse.ArgumentParser()
    parser.add_argument("--power", type=float, default=0)

    args = parser.parse_args()
    
    #Add parsed argument to main
    main(power=args.power)