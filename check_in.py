# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import logging
import webbrowser
import os
import json
import socketserver
from http.server import HTTPServer, BaseHTTPRequestHandler

from tertium_serial_handler import TertiumReader, TertiumError

# ---------------------------------------------------------------------------
#  LAYOUT MEMORIA — 2 word × 16 bit = 32 bit totali (bank 03, User Memory)
#
#  Word 0 (bit 15..0)  — Stato e indici
#  ┌─────┬─────┬──────────┬───┬───┬───┬─────┐
#  │15-12│11-8 │   7-6    │ 5 │ 4 │ 3 │ 2-0 │
#  │VOLO │IDOP │PIPELINE  │SEC│AER│NST│ RSV │
#  │4bit │4bit │  2bit    │1b │1b │1b │ 3b  │
#  └─────┴─────┴──────────┴───┴───┴───┴─────┘
#
#  Word 1 (bit 31..16) — ID Passeggero (16 bit, 0-65535)
#
#  PIPELINE: 0=nessuno, 1=checkin fatto, 2=sicurezza ok, 3=smistato
#  VOLO:     indice 0-15 → lookup.json["voli"]
#  IDOP:     indice 0-15 → lookup.json["operatori"]
#  PAXID:    chiave → lookup.json["passeggeri"][str(id)] = {nome, tel}
# ---------------------------------------------------------------------------

PIPELINE_LABELS = {0: 'Nessuno', 1: 'Check-in', 2: 'Sicurezza', 3: 'Smistato'}

LOOKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lookup.json')

def load_lookup():
    try:
        with open(LOOKUP_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {"voli": [], "operatori": [], "passeggeri": {}}

def save_lookup(data):
    with open(LOOKUP_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def decode_tag(hex8):
    hex8 = hex8.ljust(8, '0')[:8].upper()
    w0   = int(hex8[0:4], 16)
    w1   = int(hex8[4:8], 16)
    return {
        'volo_idx':  (w0 >> 12) & 0xF,
        'idop_idx':  (w0 >>  8) & 0xF,
        'pipeline':  (w0 >>  6) & 0x3,
        'sec_ok':    bool((w0 >> 5) & 1),
        'aer_ok':    bool((w0 >> 4) & 1),
        'nst_ok':    bool((w0 >> 3) & 1),
        'pax_id':    w1,
    }

# ---------------------------------------------------------------------------
payload_to_write  = None
current_tag_hex   = "00000000"
memory_ready      = False

# ---------------------------------------------------------------------------
class WebAppRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global current_tag_hex, memory_ready
        if self.path == '/tag-data':
            lk = load_lookup()
            d  = decode_tag(current_tag_hex)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'tagHex':  current_tag_hex,
                'decoded': d,
                'lookup':  lk,
                'ready':   memory_ready,
            }).encode('utf-8'))
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        global payload_to_write
        length = int(self.headers['Content-Length'])
        raw    = self.rfile.read(length)
        try:
            data = json.loads(raw.decode('utf-8'))
            if 'newPax' in data:
                lk = load_lookup()
                lk['passeggeri'][str(data['newPax']['id'])] = {
                    'nome': data['newPax']['nome'],
                    'tel':  data['newPax']['tel'],
                }
                save_lookup(lk)
            if 'hexPayload' in data:
                payload_to_write = data['hexPayload']
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        except Exception as e:
            self.send_response(500); self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'X-Requested-With, Content-type')
        self.end_headers()

    def log_message(self, *a): pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True

def start_server():
    try:
        ThreadedHTTPServer(('', 8080), WebAppRequestHandler).serve_forever()
    except OSError as e:
        print(f"ERRORE SERVER 8080: {e}")

# ---------------------------------------------------------------------------
class RFIDMultiRoleGatewayApp:
    def __init__(self, root):
        self.root    = root
        self.root.title("RFID Airport Hub")
        self.root.geometry("600x600")
        self.root.configure(padx=25, pady=25)

        self.PORT    = '/dev/ttyUSB0'
        self.power   = 0
        self.state   = 'SETUP'
        self.running = True
        self.current_epc = None

        self.ROLES = [
            "Check-in (Setup iniziale)",
            "Controllo sicurezza",
            "Smistamento aereo",
            "Smistamento nastro",
            "Lost & Found"
        ]

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s  %(message)s',
            datefmt='%H:%M:%S.%f'[:-3]
        )
        self.logger = logging.getLogger("Hub")

        self.setup_ui()
        threading.Thread(target=start_server,         daemon=True).start()
        threading.Thread(target=self.rfid_worker,     daemon=True).start()
        threading.Thread(target=self.monitor_payload, daemon=True).start()

    # ------------------------------------------------------------------
    def _log(self, msg, level='info'):
        """Log con separatore visivo per leggibilità."""
        getattr(self.logger, level)(msg)

    def _log_separator(self):
        self.logger.info("─" * 60)

    def monitor_payload(self):
        global payload_to_write
        while self.running:
            if payload_to_write and self.state == 'WAITING_INPUT' and self.current_epc:
                self.root.after(0, self.update_status, "Scrittura in corso...", "darkorange")
                self.state = 'WRITING'
            time.sleep(0.3)

    def setup_ui(self):
        tk.Label(self.root, text="RFID Airport Logistics Hub",
                 font=("Helvetica", 18, "bold")).pack(pady=(0,5))
        fr = tk.LabelFrame(self.root, text="1. Seleziona Ruolo",
                           font=("Helvetica",10,"bold"), padx=15, pady=15)
        fr.pack(fill="x", pady=(0,15))
        self.combo_ruolo = ttk.Combobox(fr, values=self.ROLES, state="readonly", width=40)
        self.combo_ruolo.current(0)
        self.combo_ruolo.grid(row=0, column=0)
        tk.Button(fr, text="Avvia Antenna", bg="#0f766e", fg="white",
                  command=self.start_operation).grid(row=0, column=1, padx=(15,0))
        fs = tk.LabelFrame(self.root, text="2. Stato",
                           font=("Helvetica",10,"bold"), padx=15, pady=15)
        fs.pack(fill="x", pady=(0,15))
        self.status_var = tk.StringVar(value="In attesa...")
        self.status_label = tk.Label(fs, textvariable=self.status_var, fg="#b45309")
        self.status_label.pack(fill="x")
        self.epc_var = tk.StringVar(value="Nessun tag")
        tk.Label(fs, textvariable=self.epc_var,
                 font=("Courier",11,"bold"), fg="#0f766e").pack(fill="x")
        self.mem_var = tk.StringVar(value="")
        tk.Label(fs, textvariable=self.mem_var,
                 font=("Courier",10), fg="#334155").pack(fill="x")
        tk.LabelFrame(self.root, text="3. Azioni",
                      font=("Helvetica",10,"bold"), padx=15, pady=15).pack(fill="x", pady=(0,10))
        self.btn_reset = tk.Button(self.root, text="Prossimo Tag",
                                   command=self.reset_scan, state="disabled")
        self.btn_reset.pack(fill="x", ipady=3)

    def update_status(self, text, color="black"):
        self.status_var.set(text)
        self.status_label.config(fg=color)

    def start_operation(self):
        if self.state == 'SETUP':
            role_name = self.ROLES[self.combo_ruolo.current()]
            self.combo_ruolo.config(state="disabled")
            self.state = 'SCANNING'
            self.update_status("Antenna attiva...", "#0f766e")
            self.btn_reset.config(state="normal")
            self._log_separator()
            self._log(f"▶  Ruolo attivo: {role_name}")
            self._log(f"   Antenna avviata su {self.PORT} — in ascolto per tag...")

    def tag_found_callback(self, epc, hex8):
        self.epc_var.set(epc)
        d  = decode_tag(hex8)
        lk = load_lookup()
        volo = lk['voli'][d['volo_idx']] if d['volo_idx'] < len(lk['voli']) else f"IDX{d['volo_idx']}"
        pax  = lk['passeggeri'].get(str(d['pax_id']), {})
        nome = pax.get('nome', '— non registrato')
        self.mem_var.set(f"Hex:{hex8}  Volo:{volo}  Pax:{nome}  Pipeline:{d['pipeline']}")

        role  = self.combo_ruolo.current()
        cdir  = os.path.dirname(os.path.abspath(__file__))
        pages = ['checkin.html', 'sicurezza.html',
                 'smistamento_aereo.html', 'smistamento_nastro.html', 'lost_found.html']
        page  = pages[role] if role < len(pages) else pages[0]
        url   = f"file://{os.path.join(cdir, page)}?epc={epc}"

        self._log(f"   Apertura browser → {page}")
        self.update_status("Tag letto. Pagina aperta.", "#15803d")
        webbrowser.open(url)

    def write_result_callback(self, success):
        if success:
            self._log("✔  Scrittura completata con successo.")
            self._log_separator()
            messagebox.showinfo("Successo", "Tag aggiornato.")
            self.reset_scan()
        else:
            self._log("✘  Scrittura fallita — operatore notificato.", level='error')
            messagebox.showerror("Errore", "Scrittura fallita.")
            self.state = 'WAITING_INPUT'

    def reset_scan(self):
        global payload_to_write, current_tag_hex, memory_ready
        self.epc_var.set("Nessun tag")
        self.mem_var.set("")
        self.current_epc = None
        payload_to_write = None
        current_tag_hex  = "00000000"
        memory_ready     = False
        if self.state in ['WAITING_INPUT', 'WRITING']:
            self.state = 'SCANNING'
            self.update_status("Antenna in ascolto...", "#0f766e")
            self._log("   Reset completato — in attesa del prossimo tag.")

    # -----------------------------------------------------------------------
    def rfid_worker(self):
        global payload_to_write, current_tag_hex, memory_ready

        def safe_read(ser, epc, address, block_num, timeout_ms=500):
            """
            Legge bank 03 leggendo esattamente frame_len byte dal campo LL
            del frame di risposta — immune a 0x0D embedded nei dati.
            """
            timeout_hex = f"{int(timeout_ms/100):02X}"
            params = f"{timeout_hex}{epc}03{address}{block_num}"
            cmd    = f"$:{6+len(params):02X}00{TertiumReader.CMD_READ_BANK}{params}\r"
            ser.reset_input_buffer()
            ser.write(cmd.encode('ascii'))
            header = ser.read(6).decode('ascii', errors='replace')
            if not header.startswith('$:'):
                self._log(f"   [SERIALE] Header inatteso: {repr(header)}", level='warning')
                return None
            try:
                frame_len = int(header[2:4], 16)
            except ValueError:
                return None
            body    = ser.read(frame_len - 4 + 1).decode('ascii', errors='replace').rstrip('\r')
            retcode = body[0:2] if len(body) >= 2 else "??"
            if retcode != "00":
                self._log(f"   [READ] Bank 03 addr={address} → errore retcode={retcode}", level='warning')
                return None
            data = body[2:]
            return data if data else None

        def read_user_memory(epc):
            """Legge Word 0 e Word 1 (8 hex chars totali) da bank 03."""
            self._log(f"   [READ] Bank 03 · Word 0 (addr=00, flag+indici)...")
            w0 = safe_read(ser, epc, "00", "01")
            if not w0 or len(w0) < 4:
                self._log("   [READ] Word 0 non leggibile.", level='warning')
                return None
            self._log(f"   [READ] Word 0 = {w0[:4]}  (raw OK)")

            self._log(f"   [READ] Bank 03 · Word 1 (addr=01, PaxID)...")
            w1 = safe_read(ser, epc, "01", "01")
            if w1 and len(w1) >= 4:
                self._log(f"   [READ] Word 1 = {w1[:4]}  (raw OK)")
                word1 = w1[:4]
            else:
                self._log("   [READ] Word 1 non leggibile, uso 0000.", level='warning')
                word1 = "0000"

            return w0[:4] + word1

        def log_decoded(hex8):
            """Stampa una riga di riepilogo human-readable del contenuto del tag."""
            d  = decode_tag(hex8)
            lk = load_lookup()
            volo = lk['voli'][d['volo_idx']] if d['volo_idx'] < len(lk['voli']) else f"IDX{d['volo_idx']}"
            op   = lk['operatori'][d['idop_idx']] if d['idop_idx'] < len(lk['operatori']) else f"IDX{d['idop_idx']}"
            pax  = lk['passeggeri'].get(str(d['pax_id']), {})
            nome = pax.get('nome', '— non registrato')
            pipe = PIPELINE_LABELS.get(d['pipeline'], str(d['pipeline']))
            flags = (f"sec={'✔' if d['sec_ok'] else '✘'}  "
                     f"aer={'✔' if d['aer_ok'] else '✘'}  "
                     f"nst={'✔' if d['nst_ok'] else '✘'}")
            self._log(f"   ┌─ Contenuto tag ───────────────────────────")
            self._log(f"   │  Hex      : {hex8}")
            self._log(f"   │  Volo     : {volo} (idx={d['volo_idx']})")
            self._log(f"   │  Operatore: {op} (idx={d['idop_idx']})")
            self._log(f"   │  Pax      : {nome} (ID={d['pax_id']})")
            self._log(f"   │  Pipeline : {pipe} ({d['pipeline']})")
            self._log(f"   │  Flag     : {flags}")
            self._log(f"   └───────────────────────────────────────────")

        def write_user_memory(epc, hex8):
            """Scrive Word 0 e Word 1 su bank 03 una word alla volta."""
            hex8 = hex8.ljust(8, '0')[:8].upper()
            d    = decode_tag(hex8)
            lk   = load_lookup()
            volo = lk['voli'][d['volo_idx']] if d['volo_idx'] < len(lk['voli']) else f"IDX{d['volo_idx']}"
            pax  = lk['passeggeri'].get(str(d['pax_id']), {})
            nome = pax.get('nome', '— non registrato')
            pipe = PIPELINE_LABELS.get(d['pipeline'], str(d['pipeline']))

            self._log(f"   [WRITE] Inizio scrittura tag {epc}")
            self._log(f"   [WRITE] Payload: {hex8}  →  volo={volo}  pax={nome}  pipeline={pipe}")

            for w in range(2):
                addr      = f"{w:02X}"
                word_data = hex8[w*4 : w*4+4]
                label     = "flag+indici" if w == 0 else "PaxID"
                self._log(f"   [WRITE] Bank 03 · Word {w} (addr={addr}, {label}) = {word_data}...")
                resp = reader.write_memory(epc, word_data,
                                           mem_bank="03", address=addr, block_num="01")
                if resp:
                    self._log(f"   [WRITE] Word {w} → OK")
                else:
                    self._log(f"   [WRITE] Word {w} → FALLITA", level='error')
                    return False
            return True

        # ---- Inizializzazione reader ----------------------------------------
        try:
            with TertiumReader(port=self.PORT) as reader:
                ser = reader.ser
                self._log_separator()
                self._log(f"[INIT] Connessione seriale su {self.PORT}")

                if ser:
                    ser.reset_input_buffer()
                    self._log("[INIT] Buffer seriale pulito")
                    reader.set_led(red_status="FF")
                    reader.set_operation_mode(mode="00")
                    self._log("[INIT] Modalità sincrona (mode=00) impostata")
                    reader.set_power(power_val=self.power)
                    self._log(f"[INIT] Potenza impostata: {self.power} (0x00 = max 27 dBm)")

                if not reader.get_status():
                    self._log("[INIT] ✘ Reader non risponde — verifica connessione.", level='error')
                    return

                self._log("[INIT] ✔ Reader pronto")
                reader.set_led(green_status="FF", red_status="00")
                self._log_separator()

                # ---- Loop principale ----------------------------------------
                while self.running:
                    if self.state == 'SCANNING':
                        self._log("[SCAN] Avvio inventory (timeout 2000 ms)...")
                        tags = reader.inventory(2000)

                        if not tags:
                            self._log("[SCAN] Nessun tag nel campo RF.")
                            time.sleep(0.5)
                            continue

                        self._log(f"[SCAN] {len(tags)} tag rilevato/i nel campo RF")
                        epc = tags[0][0] if isinstance(tags[0], tuple) else tags[0]
                        self._log_separator()
                        self._log(f"[TAG]  EPC: {epc}")
                        self.current_epc = epc
                        reader.beep(freq_hz=2000, duration_ms=100)

                        self._log("[READ] Lettura User Memory (bank 03, 2 word = 32 bit)...")
                        mem = read_user_memory(epc)

                        if mem:
                            current_tag_hex = mem
                            log_decoded(mem)
                        else:
                            current_tag_hex = "00000000"
                            self._log("[READ] ✘ Lettura fallita — memoria azzerata.", level='warning')

                        memory_ready = True
                        self.state   = 'WAITING_INPUT'
                        self._log("[SYS]  Stato → WAITING_INPUT · in attesa dell'operatore...")
                        self.root.after(0, self.tag_found_callback, epc, current_tag_hex)

                    elif self.state == 'WRITING':
                        self._log_separator()
                        self._log(f"[WRITE] Payload ricevuto dal browser: {payload_to_write}")
                        ok = write_user_memory(self.current_epc, payload_to_write)
                        payload_to_write = None
                        self.state       = 'WAITING_INPUT'

                        if ok:
                            reader.beep(freq_hz=1500, duration_ms=300)
                            self._log("[WRITE] ✔ Tag aggiornato correttamente.")
                            self.root.after(0, self.write_result_callback, True)
                        else:
                            self._log("[WRITE] ✘ Scrittura fallita.", level='error')
                            self.root.after(0, self.write_result_callback, False)
                    else:
                        time.sleep(0.1)

        except Exception as e:
            self._log(f"[ERRORE] Worker RFID: {e}", level='error')

    def on_closing(self):
        self.running = False
        self.root.destroy()
        os._exit(0)

if __name__ == "__main__":
    root = tk.Tk()
    app  = RFIDMultiRoleGatewayApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
