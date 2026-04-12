import os
import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash #for password hashing

# 1. Load Environment Variables
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
cursor = conn.cursor()

print("Wiping old insecure table...")
cursor.execute("DROP TABLE IF EXISTS users;")

print("Building new secure table...")
cursor.execute("""
    CREATE TABLE users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        password VARCHAR(255) NOT NULL 
    );
""")

print("Hashing passwords...")
# 2. Scramble the passwords BEFORE sending them to the database
ryan_secure_hash = generate_password_hash("admin123")
alice_secure_hash = generate_password_hash("pass123")

print("Saving users to database...")
insert_query = "INSERT INTO users (username, password) VALUES (%s, %s)"
cursor.execute(insert_query, ("Ryan", ryan_secure_hash))
cursor.execute(insert_query, ("Alice", alice_secure_hash))

conn.commit()
cursor.close()
conn.close()

print("✅ Success! Your database is now enterprise-grade secure.")