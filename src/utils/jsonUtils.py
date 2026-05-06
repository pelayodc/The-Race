import json
import os
import tempfile
import threading


_json_lock = threading.RLock()


def openJsonFile(filePath):
    try:
        with _json_lock:
            with open(filePath, 'r', encoding='utf-8') as jsonFile:
                data = json.load(jsonFile)
        return data
    except FileNotFoundError:
        print(f"File '{filePath}' not found.")
        return None
    except json.JSONDecodeError:
        print(f"Error decoding JSON in file '{filePath}'.")
        return None


def writeToJsonFile(filePath, data):
    directory = os.path.dirname(os.path.abspath(filePath)) or "."
    os.makedirs(directory, exist_ok=True)
    tempPath = None
    try:
        with _json_lock:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=directory, delete=False) as tempFile:
                tempPath = tempFile.name
                json.dump(data, tempFile, indent=2, ensure_ascii=False)
                tempFile.write("\n")
                tempFile.flush()
                os.fsync(tempFile.fileno())
            os.replace(tempPath, filePath)
    except (TypeError, OSError) as error:
        if tempPath and os.path.exists(tempPath):
            try:
                os.unlink(tempPath)
            except OSError:
                pass
        print(f"Error writing JSON data to file '{filePath}': {error}")
