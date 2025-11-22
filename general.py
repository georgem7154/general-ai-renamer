import os
import re
import json
import time
import sys
import threading
import uuid
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai.errors import APIError

# --- CUSTOMTKINTER IMPORTS ---
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

# --- CONFIGURATION ---
CONFIG_FILE = "config.txt" # [CONFIG CHANGE] New configuration file name
GEMINI_MODEL = "gemini-2.5-flash"
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp')
PROCESSED_SUFFIX = "_DESC" 
BATCH_SIZE = 10 

# --- RETRY CONFIGURATION ---
MAX_RETRIES = 3 
RETRY_DELAY = 10 

# --- LOG COLORS & UI COLORS (Hex codes for consistency) ---
COLOR_SUCCESS = "#27AE60"   # Green (Log)
COLOR_ERROR = "#C0392B"     # Dark Red (Log)
COLOR_INFO = "#2980B9"      # Blue (Primary Log)
COLOR_RETRY = "#F39C12"     # Orange (Retry Log)

# Pydantic Schema for Structured Output
class ImageDescription(BaseModel):
    """Schema for the model's output for a single image."""
    original_filename: str = Field(description="The full original filename (including extension) that this description corresponds to.")
    short_title: str = Field(description="A concise, descriptive, 3-5 word title for the image content, suitable for a filename.")

class BatchDescription(BaseModel):
    """Schema for the model's output for a batch of images."""
    descriptions: list[ImageDescription] = Field(description="A list of descriptions, one for each image provided.")


# Global variable for the Gemini client
gemini_client = None

# =========================================================================
# NEW CONFIGURATION FILE FUNCTIONS
# =========================================================================

def load_config() -> tuple[str, str]:
    """Reads API key and folder path from config.txt."""
    api_key = ""
    folder_path = os.getcwd()
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            for line in f:
                if line.startswith("GEMINI_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                elif line.startswith("DEFAULT_FOLDER_PATH="):
                    folder_path = line.split("=", 1)[1].strip()
                    if not os.path.isdir(folder_path): # Validate path
                        folder_path = os.getcwd() 
    except FileNotFoundError:
        # File will be created on save
        pass
    except Exception as e:
        print(f"Warning: Could not read {CONFIG_FILE}. Error: {e}")
        
    return api_key, folder_path

def save_config(api_key: str, folder_path: str):
    """Writes the current API key and folder path to config.txt."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            f.write(f"GEMINI_API_KEY={api_key}\n")
            f.write(f"DEFAULT_FOLDER_PATH={folder_path}\n")
    except Exception as e:
        print(f"Warning: Could not save {CONFIG_FILE}. Error: {e}")

# =========================================================================
# HELPER AND MAIN LOGIC FUNCTIONS (Unchanged)
# =========================================================================

def log_message(log_widget: scrolledtext.ScrolledText, message: str, tag: str = None):
    """Inserts a message into the log widget with an optional color tag."""
    log_widget.config(state=tk.NORMAL)
    log_widget.insert(tk.END, message, tag)
    log_widget.see(tk.END)
    log_widget.config(state=tk.DISABLED)

def get_batch_info_from_images(image_batch: list[tuple[str, Image.Image]], log_widget: scrolledtext.ScrolledText) -> dict[str, ImageDescription] | None:
    """Sends a batch of images to Gemini."""
    global gemini_client
    if gemini_client is None:
        log_message(log_widget, "FATAL: Gemini client not initialized. Check API Key.\n", "error")
        return None

    contents = []
    
    batch_prompt_text = (
        "Analyze the following batch of images. For EACH image, you MUST identify its "
        "original filename and generate a concise, descriptive, 3-5 word title suitable for renaming. "
        "Return the complete structured JSON array containing the 'original_filename' and 'short_title' "
        "for every image in the batch. Ensure the 'original_filename' exactly matches one of the filenames provided in the prompts."
    )
    contents.append(batch_prompt_text)

    for filename, img in image_batch:
        contents.append(img)
        contents.append(f"Image File: {filename}")
    
    retries = 0
    while retries < MAX_RETRIES:
        try:
            log_message(log_widget, f"  -> Sending batch of {len(image_batch)} images to Gemini...\n", "info")
            
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction="You are an expert file naming assistant. Your only output must be the requested JSON structure.",
                    response_mime_type="application/json",
                    response_schema=BatchDescription,
                )
            )
            
            json_data = json.loads(response.text)
            batch_result = BatchDescription(**json_data)
            
            result_map = {}
            for desc in batch_result.descriptions:
                result_map[desc.original_filename] = desc
                
            return result_map

        except APIError as e:
            retries += 1
            log_message(log_widget, f"  -> GEMINI API ERROR (Attempt {retries}/{MAX_RETRIES}): {e}\n", "error")
            if retries < MAX_RETRIES:
                log_message(log_widget, f"  -> Retrying in {RETRY_DELAY} seconds...\n", "info")
                time.sleep(RETRY_DELAY)
            else:
                return None 
        
        except (json.JSONDecodeError, AttributeError, KeyError) as e:
            log_message(log_widget, f"  -> JSON/PARSING ERROR: Could not process API response: {e}. Skipping batch.\n", "error")
            return None
        
        except Exception as e:
            log_message(log_widget, f"  -> UNEXPECTED GEMINI ERROR: {e}. Skipping batch.\n", "error")
            return None
    return None

def retry_failed_file(folder_path: str, original_filename: str, log_widget: scrolledtext.ScrolledText) -> bool:
    """Renames the file to a simple, unique name, retries the single API call, and performs final rename/cleanup."""
    global gemini_client
    if gemini_client is None: return False

    log_message(log_widget, f"\n[RETRY] Attempting retry for: {original_filename}\n", "retry")
    
    ext = os.path.splitext(original_filename)[1]
    temp_unique_id = uuid.uuid4().hex[:8]
    temp_filename = f"temp_retry_{temp_unique_id}{ext}"
    
    original_file_path = os.path.join(folder_path, original_filename)
    temp_file_path = os.path.join(folder_path, temp_filename)

    try:
        os.rename(original_file_path, temp_file_path)
        log_message(log_widget, f"  -> Temporarily renamed to: {temp_filename}\n", "retry")

        img = Image.open(temp_file_path)
        
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[img, f"Analyze this image and give a concise, descriptive, 3-5 word title. Return only the title in a simple JSON format like: {{\"short_title\": \"your title\"}}"],
            config=types.GenerateContentConfig(
                system_instruction="You are an expert file naming assistant. Your only output must be a single JSON object with the key 'short_title'.",
                response_mime_type="application/json",
            )
        )
        
        json_data = json.loads(response.text)
        short_title = json_data.get('short_title')
        
        if not short_title:
             raise ValueError("API returned no short_title.")

        cleaned_title = re.sub(r'[^\w\s-]', '', short_title).strip().replace(' ', '_').replace('-', '_')
        new_base_name = f"{cleaned_title}{PROCESSED_SUFFIX}"
        new_filename = f"{new_base_name}{ext}"
        new_file_path = os.path.join(folder_path, new_filename) 

        counter = 1
        while os.path.exists(new_file_path):
            new_filename = f"{new_base_name}_{counter}{ext}"
            new_file_path = os.path.join(folder_path, new_filename)
            counter += 1

        os.rename(temp_file_path, new_file_path) 
        
        if counter > 1:
            log_message(log_widget, f"  -> RETRY SUCCESS (Conflict Resolved): {new_filename}\n", "success")
        else:
            log_message(log_widget, f"  -> RETRY SUCCESS: {new_filename}\n", "success")
            
        return True

    except Exception as e:
        log_message(log_widget, f"  -> RETRY FAILED for {original_filename}: {e}\n", "error")
        # CRITICAL: Rename the file back to its original name on failure
        try:
            os.rename(temp_file_path, original_file_path)
            log_message(log_widget, f"  -> File restored to: {original_filename}\n", "error")
        except:
             log_message(log_widget, "  -> FATAL: Could not restore file name. Check permissions.\n", "error")
        return False


def rename_images_in_directory(folder_path: str, log_widget: scrolledtext.ScrolledText, use_delay: bool):
    """Iterates over image files in batches, then retries failed files individually."""
    
    log_message(log_widget, f"Starting batch renaming in: {folder_path}\n", "info")
    
    all_files = [
        f for f in os.listdir(folder_path) 
        if f.lower().endswith(IMAGE_EXTENSIONS) and PROCESSED_SUFFIX not in f
    ]
    
    total_eligible_files = len(all_files)
    batch_processed_count = 0
    file_index = 0
    failed_files_for_retry = []
    
    log_message(log_widget, f"Total eligible files found: {total_eligible_files}\n", "info")

    # =========================================================================
    # A. BATCH PROCESSING LOOP
    # =========================================================================
    while file_index < total_eligible_files:
        current_batch_files = all_files[file_index:file_index + BATCH_SIZE]
        image_batch = []
        
        # Load Images for the Batch
        for filename in current_batch_files:
            file_path = os.path.join(folder_path, filename)
            try:
                img = Image.open(file_path)
                image_batch.append((filename, img))
            except Exception as e:
                log_message(log_widget, f"  -> Error loading {filename}: {e}. Will retry individually.\n", "error")
                failed_files_for_retry.append(filename)
        
        if image_batch:
            # Gemini Analysis (Batch Upload)
            result_map = get_batch_info_from_images(image_batch, log_widget)
            
            if result_map:
                # Process Results and Rename Files
                for original_filename, _ in image_batch: 
                    if original_filename not in result_map:
                        log_message(log_widget, f"  -> WARNING: Missing result for {original_filename}. Marking for individual retry.\n", "error")
                        failed_files_for_retry.append(original_filename)
                        continue
                        
                    description_info = result_map[original_filename]
                    file_path = os.path.join(folder_path, original_filename)

                    # Standard Rename Logic with Conflict Resolution
                    try:
                        ext = os.path.splitext(original_filename)[1]
                        cleaned_title = re.sub(r'[^\w\s-]', '', description_info.short_title).strip().replace(' ', '_').replace('-', '_')
                        new_base_name = f"{cleaned_title}{PROCESSED_SUFFIX}"
                        new_filename = f"{new_base_name}{ext}"
                        new_file_path = os.path.join(folder_path, new_filename) 

                        counter = 1
                        while os.path.exists(new_file_path):
                            new_filename = f"{new_base_name}_{counter}{ext}"
                            new_file_path = os.path.join(folder_path, new_filename)
                            counter += 1

                        os.rename(file_path, new_file_path) 
                        
                        if counter > 1:
                            log_message(log_widget, f"  -> RENAME SUCCESS (Conflict Resolved): {new_filename}\n", "success")
                        else:
                            log_message(log_widget, f"  -> RENAME SUCCESS: {new_filename}\n", "success")
                            
                        batch_processed_count += 1

                    except Exception as e:
                        log_message(log_widget, f"  -> RENAME ERROR on file {original_filename}: {e}. Marking for individual retry.\n", "error")
                        failed_files_for_retry.append(original_filename)
            else:
                 log_message(log_widget, f"Batch API failed to return any valid results. Marking {len(image_batch)} files for individual retry.\n", "error")
                 failed_files_for_retry.extend(current_batch_files)


        file_index += BATCH_SIZE
        
        if use_delay and file_index < total_eligible_files:
            log_message(log_widget, f"--- Pausing for 5 seconds before next batch ---\n", "info")
            time.sleep(5) 

    # =========================================================================
    # B. INDIVIDUAL RETRY LOOP (Handling failures from the batch run)
    # =========================================================================
    retry_processed_count = 0
    unique_failed_files = sorted(list(set(failed_files_for_retry)))
    
    if unique_failed_files:
        log_message(log_widget, f"\n\n--- STARTING INDIVIDUAL RETRY PROCESS ({len(unique_failed_files)} files) ---\n", "retry")
        for filename in unique_failed_files:
            if retry_failed_file(folder_path, filename, log_widget):
                retry_processed_count += 1
                
    
    # =========================================================================
    # C. FINAL SUMMARY
    # =========================================================================
    total_renamed = batch_processed_count + retry_processed_count
    total_failed = total_eligible_files - total_renamed

    log_message(log_widget, f"\n\n--- FINAL RENAME PROCESS COMPLETE ---\n", "info")
    log_message(log_widget, f"Total Eligible Files: {total_eligible_files}\n", "info")
    log_message(log_widget, f"Successfully Renamed (Batch + Retry): {total_renamed}\n", "success")
    log_message(log_widget, f"Files that remain untouched: {total_failed}\n", "error")
    messagebox.showinfo("Process Complete", f"Successfully renamed {total_renamed} files.")


# 4. CustomTkinter UI Implementation
class ImageRenamerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # CTk Theme Setup
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue") 
        
        self.title("Batch AI Image Renamer")
        
        # [CONFIG CHANGE] Load default values
        default_api_key, default_folder_path = load_config()
        
        self.api_key_var = tk.StringVar(self, value=default_api_key) # Pre-populate with saved key
        self.folder_path_var = tk.StringVar(self, value=default_folder_path) # Pre-populate with saved path
        self.use_delay_var = tk.BooleanVar(self, value=True) 
        
        global gemini_client
        gemini_client = None

        self.setup_ui()

    def setup_ui(self):
        # Configure grid for resizing
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Main Title Label
        ctk.CTkLabel(self, text=f"🖼️ Batch AI Renamer (Batch Size: {BATCH_SIZE}) 🤖", 
                     font=ctk.CTkFont(size=30, weight="bold")).grid(row=0, column=0, pady=(20, 10), padx=20, sticky="ew")

        # Frame for controls (Input Frame)
        control_frame = ctk.CTkFrame(self)
        control_frame.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        control_frame.grid_columnconfigure(1, weight=1)

        # 1. API Key Input (ROW 0)
        ctk.CTkLabel(control_frame, text="Gemini API Key:").grid(row=0, column=0, padx=10, pady=5, sticky="w")
        api_entry = ctk.CTkEntry(control_frame, textvariable=self.api_key_var, show='*', width=350)
        api_entry.grid(row=0, column=1, padx=10, pady=5, sticky="ew")
        self.api_key_var.trace_add("write", self.update_client)

        # 2. Folder Path Input & Browse Button (ROW 1)
        ctk.CTkLabel(control_frame, text="Images Folder:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkEntry(control_frame, textvariable=self.folder_path_var, state='readonly', width=350).grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        
        # Browse Button for Folder
        ctk.CTkButton(control_frame, 
                      text="Browse Folder", 
                      command=self.browse_folder,
                      fg_color="#15F000",
                      hover_color="#15CC00"
                      ).grid(row=1, column=2, padx=10, pady=5)

        # 3. Processing Options Checkbox (ROW 2)
        ctk.CTkCheckBox(control_frame, 
                        text="Add 5-second pause between BATCHES (Recommended to avoid API rate limits)", 
                        variable=self.use_delay_var, 
                        onvalue=True, offvalue=False
                        ).grid(row=2, column=0, columnspan=3, pady=(10, 5), sticky="w", padx=10)

        # 4. Start Button (ROW 3)
        self.start_button = ctk.CTkButton(control_frame, text="START BATCH PROCESSING", command=self.start_processing, 
                                          fg_color="#15F000", hover_color="#15CC00", 
                                          font=ctk.CTkFont(size=24, weight="bold"))
        self.start_button.grid(row=3, column=0, columnspan=3, pady=(10, 20), padx=10, sticky="ew") 

        # 5. Log Output Label (ROW 2 of the main grid)
        ctk.CTkLabel(self, text="Processing Log:").grid(row=2, column=0, padx=20, pady=(10, 0), sticky="sw")
        
        # ScrolledText for Log (ROW 3 of the main grid)
        self.log_widget = scrolledtext.ScrolledText(self, wrap=tk.WORD, height=15, state='disabled', 
                                                    bg=ctk.ThemeManager.theme['CTkEntry']['fg_color'][0], 
                                                    fg=ctk.ThemeManager.theme['CTkEntry']['text_color'][0],
                                                    bd=0, relief=tk.FLAT)
        self.log_widget.grid(row=3, column=0, padx=20, pady=(5, 20), sticky="nsew")
        
        # Configure color tags for the log widget
        self.log_widget.tag_config('success', foreground=COLOR_SUCCESS, font=('Courier', 10, 'bold'))
        self.log_widget.tag_config('error', foreground=COLOR_ERROR, font=('Courier', 10, 'bold'))
        self.log_widget.tag_config('info', foreground=COLOR_INFO)
        self.log_widget.tag_config('retry', foreground=COLOR_RETRY, font=('Courier', 10, 'bold'))
        
        self.update_client()

    def browse_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.folder_path_var.set(folder_selected)

    def update_client(self, *args):
        """Initializes the Gemini client or updates the status based on the key."""
        global gemini_client
        api_key = self.api_key_var.get()
        
        gemini_client = None
        
        if len(api_key) > 10:
            try:
                gemini_client = genai.Client(api_key=api_key)
                self.start_button.configure(state=tk.NORMAL, text="START BATCH PROCESSING (Ready)", fg_color="#15F000", hover_color="#15CC00")
            except Exception:
                self.start_button.configure(state=tk.DISABLED, text="START BATCH PROCESSING (API Key Error)", fg_color=COLOR_ERROR, hover_color="#A93226")
        else:
            self.start_button.configure(state=tk.DISABLED, text="START BATCH PROCESSING (Enter API Key)", fg_color="gray", hover_color="dim gray")

    def start_processing(self):
        """Starts the main processing thread AND saves the current configuration."""
        
        folder = self.folder_path_var.get()
        api_key = self.api_key_var.get() # [CONFIG CHANGE] Get key for saving
        use_delay = self.use_delay_var.get()

        if not os.path.isdir(folder):
            messagebox.showerror("Error", "Invalid images folder path.")
            return

        if gemini_client is None:
            messagebox.showerror("Error", "Gemini API Client is not initialized. Check your key.")
            return

        # [CONFIG CHANGE] Save the valid configuration for next run
        save_config(api_key, folder)
        
        # Clear log and update UI state
        self.log_widget.config(state=tk.NORMAL)
        self.log_widget.delete(1.0, tk.END)
        self.log_widget.config(state=tk.DISABLED)

        self.start_button.configure(state=tk.DISABLED, text="PROCESSING... DO NOT CLOSE", fg_color='orange')
        
        # Run processing in a thread
        processing_thread = threading.Thread(target=self._run_processing_thread, args=(folder, self.log_widget, use_delay))
        processing_thread.start()

    def _run_processing_thread(self, folder, log_widget, use_delay):
        """Internal function to run the heavy processing."""
        try:
            rename_images_in_directory(folder, log_widget, use_delay) 
        finally:
            # Re-enable the button after processing finishes
            self.after(0, lambda: self.start_button.configure(state=tk.NORMAL, text="START BATCH PROCESSING (Done)", fg_color="#15F000", hover_color="#15CC00"))


if __name__ == "__main__":
    app = ImageRenamerApp()
    app.mainloop()