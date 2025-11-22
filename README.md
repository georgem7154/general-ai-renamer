# SIMPLE EXPLAINATION
So for a folder we have this image which is named as image.jpg. To know the image we will have to open and check it and then rename it. But with this you dont have to worry about it, you can process a bunch of images in one shot and it rename it for you with the power of AI

Take this image, it named as image.png at first
![image](https://github.com/user-attachments/assets/266ef246-e830-405c-8032-0b3cbc68972e)

run the software and you get it renmaed to Reindeer_in_snowy_landscape_DESC.jpg

# Installation and running
1. You can run the python file but will have to install all packages

2. run the exe which will auto install all the packages and get you ready to execute on the go, first time run might be slow

# ADVANCED EXPLAINATION
## 🏗️ Architectural Components

| Component | Role | Gemini Interaction |
| :--- | :--- | :--- |
| **CustomTkinter UI** | Provides the user interface for inputting the API key and folder path, and displays the real-time processing log. | N/A |
| **Pydantic Schemas** | Define the exact **JSON structure** the Gemini model must return for both single-file and batch requests, guaranteeing reliable data parsing. | **Enforced Output** |
| **Batch Processor** | The primary, fast loop that sends up to 10 images in a single API call to minimize latency and save API resources. | **High Volume API** |
| **Individual Retry System** | The secondary loop that handles files that failed during the batch process (usually due to naming/parsing conflicts). It temporarily renames the file to a simple name before retrying. | **Targeted API** |
| **File Conflict Resolver** | Logic that checks if a new descriptive name already exists and, if so, appends a sequential counter (e.g., `_1`, `_2`) to guarantee uniqueness. | **Final Renaming Step** |

---

## 🔁 Workflow and Processing Steps

The workflow is divided into three distinct phases to prioritize speed and reliability. 

### 1. Initialization and Preparation

1.  **UI Input:** The user provides the **Gemini API Key** and the **Target Folder Path**.
2.  **Client Setup:** The `update_client` method initializes the `gemini_client` object, verifying the API key.
3.  **Thread Start:** The `start_processing` method moves the entire renaming operation onto a separate **background thread** (`threading.Thread`) so the UI remains responsive.
4.  **File List:** The script scans the folder to create a list of all eligible images (excluding those that already have the `_DESC` suffix).

---

### 2. Primary Batch Processing (Speed Focus)

The `rename_images_in_directory` function executes the batch processing loop:

* **Batch Creation:** Files are grouped into batches of **10** (`BATCH_SIZE`).
* **Image Loading:** All files in the current batch are loaded into memory as **PIL Image objects**.
* **Batch API Call:** The `get_batch_info_from_images` function sends all 10 images and a single request to Gemini. This is faster and cheaper than 10 separate calls.
    * The prompt specifically asks Gemini to return a structured JSON list, with each entry containing the **`original_filename`** and the new **`short_title`**.
* **Result Mapping:** The returned JSON is parsed using the **Pydantic `BatchDescription` schema** and converted into a dictionary (map) where the key is the `original_filename`.
* **Rename and Conflict Check:** For each file in the batch:
    * The script attempts to look up the result using the original filename as the key.
    * If the result is found, the file is renamed using the new title.
    * The **Conflict Resolver** ensures the new name is unique by appending `_1`, `_2`, etc., if a file with the same descriptive title already exists.
* **Failure Collection:** If a file fails to load or if its result is **missing from the API map** (often due to the AI failing to echo the complex filename accurately), the file's original name is added to the `failed_files_for_retry` list.
* **Batch Pause:** A 5-second pause occurs between batches (if enabled) to prevent hitting API rate limits.

---

### 3. Individual Retry Processing (Reliability Focus)

Once all batches are complete, the system processes the `failed_files_for_retry` list:

* **Isolation:** The `retry_failed_file` function is called for each unique failed file.
* **Temporary Rename:** The complex original file name (e.g., `2025-10-20 11_18_04-[Not Saved]...`) is immediately renamed to a simple, unique temporary name (e.g., `temp_retry_a3f9c4d2.png`) using `uuid`. This guarantees the filename is easily parsable for the AI.
* **Individual API Call:** A simple, single-file API request is made using a basic JSON output instruction.
* **Final Rename:** If the AI call succeeds, the file is renamed from the temporary name to its final descriptive name (using the same Conflict Resolver logic).
* **CRITICAL Cleanup:** If the individual API call or renaming **fails**, the script renames the file back from its temporary name to its **original, complex filename**. This prevents data loss or user confusion.

This three-phase architecture ensures **maximum speed** via batching while guaranteeing **high reliability** through the dedicated, protective retry mechanism.
