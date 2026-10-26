import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox

# Create the GUI window
window = tk.Tk()

# Function to handle button click and execute the conversion
def convert_csv_to_db():
    # Prompt for input file
    csv_file = filedialog.askopenfilename(title="Select CSV File", filetypes=[("CSV Files", "*.csv")])

    # Prompt for output directory
    output_directory = filedialog.askdirectory(title="Select Output Directory")

    # Prompt for output file name (without extension)
    output_name = tk.simpledialog.askstring("Output File Name", "Enter the desired name for the output database file (without extension): ")

    if csv_file and output_directory and output_name:
        # Append a unique number to the output file name
        output_file = output_name + "_01.db"
        index = 1
        while output_file in os.listdir(output_directory):
            index += 1
            output_file = f"{output_name}_{index:02d}.db"

        try:
            # Execute the csvs-to-sqlite command using subprocess
            subprocess.run(["csvs-to-sqlite", csv_file, os.path.join(output_directory, output_file)], check=True)
            messagebox.showinfo("Conversion Successful", "CSV file converted to SQLite database successfully!")
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Conversion Error", f"An error occurred during the conversion:\n{e}")
    else:
        messagebox.showwarning("Input Missing", "Please provide all required input.")

# Create the button
button = tk.Button(window, text="Convert CSV to SQLite DB", command=convert_csv_to_db)

# Configure the button position
button.pack()

# Start the GUI event loop
window.mainloop()
