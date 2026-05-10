import os
import json
import gspread
import google.generativeai as genai
import threading
from oauth2client.service_account import ServiceAccountCredentials
import psycopg2
from psycopg2.extras import RealDictCursor #returns output in a dictionary format
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
from google.oauth2.service_account import Credentials
from datetime import datetime
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

load_dotenv()

def get_gspread_client():
    # Path to your credentials file on your laptop
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('google_creds.json', scope)
    return gspread.authorize(creds)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fallback-secret-for-local-testing")
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def get_db_connection():
    try: 
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        return conn
    except Exception as e:
        print(f"Database Connection Error {e}")
        return None


@app.route('/test_db')
def test_db():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Could not connect to database. Check your password in .env!"})
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tracker_data;")
        count = cur.fetchone()[0]

        cur.close()
        conn.close()
        return jsonify({"status": "success", "message": f"Connected! The tracker_data table has {count} rows."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
            

# --- CONFIGURATION ---
SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME")
TAB_NAME = os.environ.get("GOOGLE_TAB_NAME")

def get_google_sheet():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("google_creds.json", scopes=scope)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).worksheet(TAB_NAME)

#time calculation logic function
def calculate_time_duration(start_time, end_time):
    if not start_time or not end_time:
        return ""
    try:
        start_dt = datetime.strptime(start_time, "%H:%M:%S")
        end_dt = datetime.strptime(end_time, "%H:%M:%S")
        total_seconds = int((end_dt-start_dt).total_seconds())
        if total_seconds < 0:
            total_seconds += 86400
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours} H {minutes} M"
    except ValueError:
        return "Invalid Time"


# --- ROUTES ---
#log in authentication
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- LOGIN & LOGOUT ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        form_username = request.form.get('username')
        form_password = request.form.get('password')
        
        try:
            # 1. Open Database Connection
            conn = get_db_connection()
            if not conn:
                return render_template('login.html', error="Database connection error. Please try again.")
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # 2. Search for user
            sql_query = "SELECT * FROM users WHERE username = %s"
            cursor.execute(sql_query, (form_username,))
            user = cursor.fetchone()
            
            # Close connection
            cursor.close()
            conn.close()

            # 3. Verify Credentials with Password Hashing
            if user and check_password_hash(user['password'], form_password):
                session['user'] = user['username']
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Invalid User ID or Password.")
        except Exception as e:
            return render_template('login.html', error="Database connection error. Please try again.")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None) # Erases the user from the server's memory
    return redirect(url_for('login')) # Kicks them back to the login screen

# --- PROTECTED APP ROUTES ---
@app.route('/')
@login_required
def index():
    try:
        with open('mls_data.json', 'r') as f:
            data = json.load(f)
            mls_list = data.get('mls_names', [])
    except Exception as e:
        mls_list = []
        print(f"Error loading JSON: {e}")
        
    return render_template('index.html', mls_list=sorted(mls_list), current_user=session['user'])


@app.route('/get_recent')
@login_required
def get_recent():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify([])  
        cur = conn.cursor()
        
        # Grab the latest 10 entries instantly from PostgreSQL
        cur.execute("SELECT * FROM tracker_data ORDER BY log_date DESC, time_log DESC LIMIT 10;")
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # Convert the SQL rows into a simple list of lists for your JavaScript
        formatted_records = [list(row) for row in records]
        
        return jsonify(formatted_records)
    except Exception as e:
        print(f"Database Fetch Error: {e}")
        return jsonify([])

def sync_to_sheets_task(data, current_agent, time_taken_string, log_date):
    """Runs in the background to sync data to Google Sheets."""
    try:
        # Assuming you have your existing get_google_sheet() helper function
        sheet = get_google_sheet()
        hid = data.get('hid')
        
        new_row = [
            hid,
            current_agent,
            data.get('mls_name', ''),
            data.get('prop_type'),      
            data.get('home_type', ''),     
            data.get('listing_date', ''),  
            data.get('status', ''),        
            time_taken_string,         
            log_date
        ]

        cell = sheet.find(str(hid))
        if cell:
            range_label = f"A{cell.row}:I{cell.row}"
            sheet.update(range_label, [new_row])
            print(f"Background: Updated {hid} in Sheets.")
        else:
            sheet.append_row(new_row)
            print(f"Background: Appended {hid} to Sheets.")
            
    except Exception as e:
        print(f"Background Sheet Sync Error: {e}")

@app.route('/get_all_entries')
@login_required
def get_all_entries():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify([]) 
            
        cur = conn.cursor()
        # Grab absolutely everything, newest first
        cur.execute("SELECT hid, agent, mls_name, prop_type, status, time_log, log_date FROM tracker_data ORDER BY log_date DESC;")
        records = cur.fetchall()
        
        cur.close()
        conn.close()
        
        formatted_records = [list(row) for row in records]
        return jsonify(formatted_records)
    except Exception as e:
        print(f"Master Ledger Fetch Error: {e}")
        return jsonify([])
    
# --- TIME PARSING HELPER ---
def parse_time_to_minutes(time_str):
    if not time_str or time_str == "Invalid Time" or "H" not in time_str:
        return None
    try:
        parts = time_str.split('H')
        hours = int(parts[0].strip())
        minutes = int(parts[1].replace('M', '').strip())
        return (hours * 60) + minutes
    except:
        return None

def minutes_to_string(minutes):
    if minutes is None or minutes == 0: return "--"
    return f"{int(minutes // 60)} H {int(minutes % 60)} M"

# --- UPGRADED INSIGHTS ROUTE ---
@app.route('/api/insights')
@login_required
def get_insights():
    try:
        conn = get_db_connection()
        if not conn: return jsonify({"error": "Database unavailable"}), 500
            
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT mls_name, status, time_log FROM tracker_data;")
        records = cur.fetchall()
        cur.close()
        conn.close()

        total = len(records)
        if total == 0: return jsonify({"error": "No data yet"})

        status_counts = {}
        found_times = []
        not_found_times = []
        mls_stats = {}

        for r in records:
            status = r['status'] or 'Unknown'
            mls = r['mls_name'] or 'Unknown'
            is_found = status in ['Found', 'Data Found']
            mins = parse_time_to_minutes(r['time_log'])

            # Count for Pie Chart
            status_counts[status] = status_counts.get(status, 0) + 1

            if mls not in mls_stats:
                mls_stats[mls] = {'found': [], 'not_found': []}

            if mins is not None:
                if is_found:
                    found_times.append(mins)
                    mls_stats[mls]['found'].append(mins)
                else:
                    not_found_times.append(mins)
                    mls_stats[mls]['not_found'].append(mins)

        # Global Averages
        avg_found = sum(found_times) / len(found_times) if found_times else 0
        avg_not_found = sum(not_found_times) / len(not_found_times) if not_found_times else 0

        # Calculate Fastest MLS (Must have at least 2 entries to count)
        fastest_found_mls = {"name": "--", "time": float('inf')}
        fastest_not_found_mls = {"name": "--", "time": float('inf')}
        
        # Prepare Data for Bar Chart (Top 5 MLSs by volume)
        valid_mls = {k: v for k, v in mls_stats.items() if len(v['found']) + len(v['not_found']) > 0}
        top_mls = sorted(valid_mls.items(), key=lambda x: len(x[1]['found']) + len(x[1]['not_found']), reverse=True)[:5]
        
        chart_labels = []
        chart_f_data = []
        chart_nf_data = []

        for mls, data in top_mls:
            chart_labels.append(mls)
            f_avg = sum(data['found']) / len(data['found']) if data['found'] else 0
            nf_avg = sum(data['not_found']) / len(data['not_found']) if data['not_found'] else 0
            
            chart_f_data.append(round(f_avg))
            chart_nf_data.append(round(nf_avg))

            # Check for global fastest
            if len(data['found']) >= 2 and f_avg < fastest_found_mls['time']:
                fastest_found_mls = {"name": mls, "time": f_avg}
            if len(data['not_found']) >= 2 and nf_avg < fastest_not_found_mls['time']:
                fastest_not_found_mls = {"name": mls, "time": nf_avg}

        success_rate = round((sum(1 for r in records if r['status'] in ['Found', 'Data Found']) / total) * 100)

        return jsonify({
            "total": total,
            "success_rate": success_rate,
            "avg_found": minutes_to_string(avg_found),
            "avg_not_found": minutes_to_string(avg_not_found),
            "fastest_found": f"{fastest_found_mls['name']} ({minutes_to_string(fastest_found_mls['time'])})" if fastest_found_mls['name'] != "--" else "--",
            "fastest_not_found": f"{fastest_not_found_mls['name']} ({minutes_to_string(fastest_not_found_mls['time'])})" if fastest_not_found_mls['name'] != "--" else "--",
            "status_counts": status_counts,
            "chart_labels": chart_labels,
            "chart_found": chart_f_data,
            "chart_not_found": chart_nf_data
        })
    except Exception as e:
        print(f"Insights Error: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/api/ai_summary')
@login_required
def get_ai_summary():
    try:
        # 1. Grab the raw data (Reusing the logic from your insights route)
        conn = get_db_connection()
        if not conn: return jsonify({"error": "Database unavailable"}), 500
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT mls_name, status, time_log FROM tracker_data;")
        records = cur.fetchall()
        cur.close()
        conn.close()

        # 2. Package the data into a readable string for the AI
        data_dump = f"Total records: {len(records)}. Raw Data: {records}"

        # 3. Prompt the AI (using the blazing fast 1.5 Flash model)
        prompt = f"""
        You are a senior data analyst for a real estate team. Review this operational data:
        {data_dump}
        
        Write exactly 3 short, punchy bullet points identifying operational bottlenecks, speed differences between MLS portals, or success rates. 
        Format the response in raw HTML using <li> tags. Do not use markdown. Do not include introductory text.
        """
        
        model = genai.GenerativeModel('gemini-flash-latest')
        response = model.generate_content(prompt)
        
        return jsonify({"summary": response.text})
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": "Could not generate AI summary."}), 500

@app.route('/submit', methods=['POST'])
@login_required
def submit():
    try:
        data = request.json
        hid = data.get('hid')
        current_agent = session.get('user', 'Unknown Agent')
        
        # 2. Time Math
        time_taken_string = calculate_time_duration(data.get('start_time'), data.get('end_time'))
        log_date = datetime.now().strftime("%Y-%m-%d")

        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database unavailable"}), 500
        cur = conn.cursor()

        cur.execute("""
        INSERT INTO tracker_data (hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hid) DO UPDATE SET 
                agent = EXCLUDED.agent,
                mls_name = EXCLUDED.mls_name,
                prop_type = EXCLUDED.prop_type,
                home_type = EXCLUDED.home_type,
                listing_date = EXCLUDED.listing_date,
                status = EXCLUDED.status,
                time_log = EXCLUDED.time_log,
                log_date = EXCLUDED.log_date;

        """, (hid, current_agent, data.get('mls_name', ''), data.get('prop_type'), 
              data.get('home_type', ''), data.get('listing_date', ''), data.get('status', ''), 
              time_taken_string, log_date))
        conn.commit()
        cur.close()
        conn.close()

        thread = threading.Thread(
            target=sync_to_sheets_task,
            args=(data, current_agent, time_taken_string, log_date)
        )
        thread.start()

        return jsonify({"status": "success", "message": f"Saved {hid} Instantly!"})
    except Exception as e:
        print(f"Submission Error {e}")
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/update', methods=['POST'])
@login_required
def update_entry():
    try:
        data = request.json
        hid = data.get('hid')
        current_agent = session.get('user', 'Unknown Agent')
        
        # Time Math with Function
        time_taken_string = calculate_time_duration(data.get('start_time'), data.get('end_time'))   
        log_date = datetime.now().strftime("%Y-%m-%d")

        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database unavailable"}), 500
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE tracker_data 
            SET agent=%s, mls_name=%s, prop_type=%s, home_type=%s, listing_date=%s, status=%s, time_log=%s, log_date=%s
            WHERE hid=%s;
        """, (current_agent, data.get('mls_name'), data.get('prop_type'), data.get('home_type'), 
              data.get('listing_date'), data.get('status'), time_taken_string, log_date, hid))
        
        conn.commit()
        cur.close()
        conn.close()

        # Call your background thread here to also update Google Sheets!
        thread = threading.Thread(target=sync_to_sheets_task, args=(data, current_agent, time_taken_string, log_date))
        thread.start()

        return jsonify({"status": "success", "message": "Entry updated instantly!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/delete/<hid>', methods=['DELETE'])
@login_required
def delete_entry(hid):
    try:
        # 1. Delete instantly from PostgreSQL
        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database unavailable"}), 500
        cur = conn.cursor()
        cur.execute("DELETE FROM tracker_data WHERE hid = %s;", (hid,))
        conn.commit()
        cur.close()
        conn.close()

        # 2. Delete from Google Sheets in the background
        def delete_from_sheets_bg(hid_to_delete):
            try:
                sheet = get_google_sheet()
                cell = sheet.find(str(hid_to_delete))
                if cell:
                    sheet.delete_rows(cell.row)
            except Exception as e:
                print(f"Background Sheet Delete Error: {e}")

        thread = threading.Thread(target=delete_from_sheets_bg, args=(hid,))
        thread.start()

        return jsonify({"status": "success", "message": f"HID {hid} deleted!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/import_sheets')
def import_sheets():
    try:
        sheet = get_google_sheet()
        all_records = sheet.get_all_values()[1:]

        conn = get_db_connection()
        if not conn:
            return jsonify({"status": "error", "message": "Database unavailable"}), 500
        cur = conn.cursor()

        imported_count = 0
        for row in all_records:
            # Ensure the row has enough columns to avoid index errors
            if len(row) < 9:
                row.extend([''] * (9 - len(row)))
                
            hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date = row[:9]
            
            # Insert into database. If HID exists, skip it.
            cur.execute("""
                INSERT INTO tracker_data 
                (hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (hid) DO UPDATE SET 
                    agent = EXCLUDED.agent,
                    mls_name = EXCLUDED.mls_name,
                    prop_type = EXCLUDED.prop_type,
                    home_type = EXCLUDED.home_type,
                    listing_date = EXCLUDED.listing_date,
                    status = EXCLUDED.status,
                    time_log = EXCLUDED.time_log,
                    log_date = EXCLUDED.log_date;
            """, (hid, agent, mls_name, prop_type, home_type, listing_date, status, time_log, log_date))
            imported_count += 1
            
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({"status": "success", "message": f"Imported {imported_count} records from Google Sheets into PostgreSQL!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    # host='0.0.0.0' tells Flask to broadcast to your whole Wi-Fi network
    app.run(host='0.0.0.0', port=5000, debug=True)