import os
import ast
import json

ROOT_DIR = "/Users/Raghav/Quest1/autosys-astronomer/auotsys"
EXCLUDE_DIRS = {".git", ".venv", ".pytest_cache", "autosys_clone.egg-info", "__pycache__", "explain", "data", "config", "examples"}

WORKFLOW_MAP = {
    "autosys/cli": {"order": 1, "title": "1. User Input (CLI)", "desc": "Users submit jobs or trigger events using command-line tools.", "icon": "ph-terminal"},
    "autosys/parser": {"order": 2, "title": "2. JIL Parsing", "desc": "Raw JIL text is tokenized and parsed into Python objects.", "icon": "ph-code"},
    "autosys/models": {"order": 3, "title": "3. Data Models", "desc": "Core data structures representing jobs, machines, and events.", "icon": "ph-cube"},
    "autosys/db": {"order": 4, "title": "4. Database Storage", "desc": "The Event Server persists all jobs and state changes.", "icon": "ph-database"},
    "autosys/app_server": {"order": 5, "title": "5. API Layer", "desc": "The central nervous system routing data between DB, Scheduler, and Agent.", "icon": "ph-app-window"},
    "autosys/scheduler": {"order": 6, "title": "6. Scheduler Engine", "desc": "Evaluates conditions and time triggers to start jobs.", "icon": "ph-cpu"},
    "autosys/agent": {"order": 7, "title": "7. Agent Execution", "desc": "Remote machines execute the commands and return exit codes.", "icon": "ph-terminal-window"},
    "autosys/wcc": {"order": 8, "title": "8. Web Control Center", "desc": "Web UI for monitoring the live state of the workflow.", "icon": "ph-browser"},
    "autosys/notifications": {"order": 9, "title": "9. Alerts & Alarms", "desc": "Dispatches notifications if a workflow fails.", "icon": "ph-bell"},
    "tests": {"order": 10, "title": "10. Validation (Tests)", "desc": "Test cases validating the end-to-end correctness of the workflow.", "icon": "ph-check-circle"},
    "autosys": {"order": 11, "title": "11. Core Setup", "desc": "Root initialization files.", "icon": "ph-gear"}
}

KIDS_STAGE_MAP = {
    "autosys/cli": {"title": "1. The Menu & Waiters (CLI)", "desc": "When you go to a restaurant, you speak to a waiter. These files are the Waiters. They take your order (JIL) and send it to the kitchen."},
    "autosys/parser": {"title": "2. Translating the Order (Parser)", "desc": "Sometimes customers speak a weird language. These robots translate your words into an official 'Order Ticket'."},
    "autosys/models": {"title": "3. The Ticket Shapes (Models)", "desc": "These files define exactly what a blank Order Ticket or Sticky Note looks like."},
    "autosys/db": {"title": "4. The Filing Cabinet (DB)", "desc": "We have to save every single order in a huge metal filing cabinet. These files manage the cabinet."},
    "autosys/app_server": {"title": "5. The Walkie-Talkies (API)", "desc": "The central radio tower that lets the TVs, the Manager, and the Kitchen all talk to each other without shouting."},
    "autosys/scheduler": {"title": "6. The Boss Manager (Scheduler)", "desc": "The Manager looks at the clock and the rules. He is the one who yells: 'Okay, start cooking the steak now!'"},
    "autosys/agent": {"title": "7. The Kitchen & Chef (Agent)", "desc": "The kitchen! The Chef actually cooks the food (runs the computer code) and says if it was a Success or if he burned it."},
    "autosys/wcc": {"title": "8. The TV Screens (Web UI)", "desc": "The giant TV screens in the lobby showing customers green checkmarks for finished food."},
    "autosys/notifications": {"title": "9. The Fire Alarms (Alerts)", "desc": "Loud alarms that go off if the Chef burns the food or the kitchen catches fire!"},
    "tests": {"title": "10. The Health Inspector (Tests)", "desc": "The health inspector comes in to test the whole restaurant and make sure nobody gets sick."},
    "autosys": {"title": "11. The Foundation", "desc": "The concrete blocks the restaurant is built on."}
}

KIDS_FILE_MAP = {
    "jil_cmd.py": "The waiter's notepad where you write down new jobs.",
    "autorep_cmd.py": "Asking the waiter for a status update on your food.",
    "sendevent_cmd.py": "Yelling across the restaurant to cancel an order.",
    "lexer.py": "The tiny robot that chops your sentence into individual words.",
    "jil_parser.py": "The smarter robot that understands the grammar and writes the ticket.",
    "condition_parser.py": "Translates special requests like 'Only cook this if the fries are done'.",
    "job.py": "The blank Ticket that tells us what an order needs (name, owner).",
    "event.py": "The sticky notes slapped on tickets when statuses change (Cooking -> Done).",
    "schema.py": "The metal folders inside the filing cabinet.",
    "repository.py": "The Cabinet Keeper who puts tickets in and takes them out.",
    "time_trigger.py": "The Manager checking his watch to see if it's 5:00 PM.",
    "condition_evaluator.py": "The Manager checking if the soup is done before cooking steak.",
    "state_machine.py": "The Manager's Rulebook. Orders MUST go Waiting -> Cooking -> Done.",
    "box_manager.py": "The Combo Meal Handler. If fries burn, the whole combo fails.",
    "server.py": "The Kitchen Window listening for Walkie-Talkie orders.",
    "dispatch.py": "The Head Chef deciding which stove to use.",
    "runner.py": "The Actual Chef who cooks and returns a number (0=Perfect, 1=Burned).",
    "main.py": "The Walkie-Talkie Base connecting everyone together.",
    "app.py": "The beautiful TV Screen showing all the orders.",
    "alarm_manager.py": "The Fire Alarm that rings if a job fails."
}

def get_py_info(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        lines = len(content.splitlines())
        
        tree = ast.parse(content)
        module_doc = ast.get_docstring(tree)
        
        classes = []
        functions = []
        
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                doc = ast.get_docstring(node)
                methods = [m.name for m in node.body if isinstance(m, ast.FunctionDef)]
                classes.append({
                    "name": node.name,
                    "doc": doc or "No docstring provided.",
                    "methods": methods
                })
            elif isinstance(node, ast.FunctionDef):
                doc = ast.get_docstring(node)
                functions.append({
                    "name": node.name,
                    "doc": doc or "No docstring provided."
                })
                
        return {
            "lines": lines,
            "module_doc": module_doc or "No module docstring.",
            "classes": classes,
            "functions": functions
        }
    except Exception as e:
        return {"error": str(e), "lines": 0}

def get_generic_info(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = len(f.readlines())
        return {"lines": lines}
    except Exception:
        return {"lines": "Unknown"}

def build_docs():
    data = {}
    
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        rel_dir = os.path.relpath(root, ROOT_DIR)
        rel_dir = rel_dir.replace("\\", "/")
        
        if rel_dir == ".":
            continue
            
        workflow_info = WORKFLOW_MAP.get(rel_dir)
        if not workflow_info:
            workflow_info = {"order": 99, "title": rel_dir, "desc": f"Files in {rel_dir}", "icon": "ph-folder"}
            
        kids_info = KIDS_STAGE_MAP.get(rel_dir)
        if not kids_info:
            kids_info = {"title": f"The {rel_dir} Room", "desc": "Another room in our massive restaurant."}
            
        if rel_dir not in data:
            data[rel_dir] = {
                "id": rel_dir.replace("/", "_").replace(" ", "_").lower(),
                "order": workflow_info["order"],
                "title": workflow_info["title"],
                "description": workflow_info["desc"],
                "kid_title": kids_info["title"],
                "kid_description": kids_info["desc"],
                "icon": workflow_info["icon"],
                "files": []
            }
            
        for file in files:
            if file.endswith(".pdf") or file.endswith(".pyc") or file.endswith(".db") or file.endswith(".db-shm") or file.endswith(".db-wal"):
                continue
                
            filepath = os.path.join(root, file)
            
            file_data = {
                "name": file,
                "type": "Python" if file.endswith(".py") else "File",
                "path": os.path.relpath(filepath, ROOT_DIR)
            }
            
            # Map kids file desc
            file_data["kid_desc"] = KIDS_FILE_MAP.get(file, f"A tiny worker robot helping out in the {kids_info['title']} department.")
            
            if file.endswith(".py"):
                info = get_py_info(filepath)
                file_data.update(info)
                file_data["description"] = info.get("module_doc", "No description").strip().split('\n')[0]
                if not file_data["description"] or file_data["description"] == "No module docstring.":
                    file_data["description"] = f"Python source file ({info.get('lines', 0)} lines)"
            else:
                info = get_generic_info(filepath)
                file_data.update(info)
                file_data["description"] = f"Static or configuration file ({info.get('lines', 0)} lines)"
                
            data[rel_dir]["files"].append(file_data)
            
    final_data = [v for k, v in data.items() if v["files"]]
    final_data.sort(key=lambda x: x["order"])
    
    with open(os.path.join(ROOT_DIR, "explain", "data.js"), "w", encoding="utf-8") as f:
        f.write("const codebaseData = ")
        json.dump(final_data, f, indent=4)
        f.write(";")
        
    print(f"Generated documentation for {sum(len(s['files']) for s in final_data)} files.")

if __name__ == "__main__":
    build_docs()
