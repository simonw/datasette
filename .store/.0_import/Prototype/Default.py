import os
import sqlite3
import pandas as pd
import sys

# Check if the correct number of arguments have been provided
if len(sys.argv) != 2:
    print(r"Usage: 'C:\Users\Martin\OneDrive - studiocordillera.com 1\Documents\.GITHUB_Repos\datasette\venv\Scripts\python.exe' Default.py <csv_file>")
    sys.exit(1)

# Assign arguments to variables
csv_file = sys.argv[1]
output_directory = r"C:\Users\Martin\OneDrive - studiocordillera.com 1\Documents\.GITHUB_Repos\datasette\.store\.0_import\Prototype"  # Replace with your default directory
output_name = "default_output"  # Replace with your default output name

# Preview the first 5 lines of the CSV file
df = pd.read_csv(csv_file)
print(df.head())

# Append a unique number to the output file name
index = 1
output_file = output_name + "_01.db"
while output_file in os.listdir(output_directory):
    index += 1
    output_file = f"{output_name}_{index:02d}.db"

# Create connection to SQLite database
conn = sqlite3.connect(os.path.join(output_directory, output_file))

# Use pandas to_sql() function to write records stored in DataFrame to SQLite database
df.to_sql('table_name', conn, if_exists='replace', index=False)

# Close connection
conn.close()

# Reopen the SQLite connection
conn = sqlite3.connect(os.path.join(output_directory, output_file))
# Query the database and print out the result
df_check = pd.read_sql_query("SELECT * from table_name", conn)
print(df_check)

# Close the SQLite connection again
conn.close()

print("CSV file converted to SQLite database successfully!")

# Wait for user input before exiting
input("Press any key to continue...")

