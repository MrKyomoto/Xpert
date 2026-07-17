import os
import json

def setup(registry):
    def read_file(filepath: str) -> str:
        """Read content from a file."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"
            
    def write_file(filepath: str, content: str) -> str:
        """Write content to a file."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully wrote to {filepath}"
        except Exception as e:
            return f"Error writing file: {str(e)}"
            
    def append_to_json(filepath: str, data_str: str) -> str:
        """Append a JSON string object to a JSON array file."""
        try:
            try:
                data = json.loads(data_str)
            except:
                return "Error: data_str is not valid JSON"
                
            items = []
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    try:
                        items = json.load(f)
                        if not isinstance(items, list):
                            items = [items]
                    except:
                        items = []
                        
            items.append(data)
            
            os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            return f"Successfully appended to {filepath}"
        except Exception as e:
            return f"Error appending to JSON: {str(e)}"

    registry.register("read_file", "Read content from a file", read_file)
    registry.register("write_file", "Write content to a file", write_file)
    registry.register("append_to_json", "Append a JSON object (as string) to a JSON array file", append_to_json)
