import os
from flask import Flask, render_template, request, jsonify
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# --- CONFIGURATION ---
SHEET_NAME = "Autofill Data Entry Sheet" 

def get_google_sheet():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("google_creds.json", scopes=scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1

@app.route('/')
def index():
    mls_list = ["MLS North", "MLS South", "MLS West", "MLS East", "California MLS", "Texas MLS"]
    return render_template('index.html', mls_list=sorted(mls_list))

@app.route('/get_recent', methods=['GET'])
def get_recent():
    try:
        sheet = get_google_sheet()
        all_rows = sheet.get_all_values()
        if len(all_rows) <= 1:
            return jsonify([])
        
        # Get last 10 entries (excluding header), then reverse for newest first
        recent = all_rows[1:][-10:]
        recent.reverse()
        return jsonify(recent)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/submit', methods=['POST'])
def submit():
    try:
        data = request.json
        # Time Calculation Logic
        fmt = "%H:%M:%S"
        start_dt = datetime.strptime(data.get('start_time'), fmt)
        end_dt = datetime.strptime(data.get('end_time'), fmt)
        
        duration = end_dt - start_dt
        total_seconds = int(duration.total_seconds())
        if total_seconds < 0: total_seconds += 86400 

        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60

        # --- NEW FORMATTING LOGIC ---
        # This creates a string like "1 H 23 M"
        time_taken_string = f"{hours} H {minutes} M"

        new_row = [
            data.get('hid'),
            data.get('mls_name'),
            data.get('prop_type'),
            data.get('status'),
            time_taken_string, # Combined string here
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]

        sheet = get_google_sheet()
        sheet.append_row(new_row)

        return jsonify({
            "status": "success", 
            "message": f"Saved! Duration: {time_taken_string}"
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    app.run(debug=True)