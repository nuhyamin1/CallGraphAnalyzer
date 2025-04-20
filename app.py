from flask import Flask, render_template, request, jsonify
import os # Needed for path operations
import time # Needed for unique filenames
import ast
import json
# import inspect # Not strictly needed with ast.get_source_segment

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure the upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def get_call_name(call_node):
    """Attempts to get the name of the function being called."""
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    elif isinstance(func, ast.Attribute):
        # This gives the method name (e.g., 'method').
        # Determining the class requires more complex analysis (type inference)
        # which is beyond simple AST walking. We'll return the attribute name
        # and rely on matching it with defined methods.
        return func.attr
    # Handle other cases like calls on calls, etc., if necessary
    return None

class CodeAnalyzer(ast.NodeVisitor):
    def __init__(self, code_content):
        self.code_content = code_content
        self.structure = {'name': 'root', 'id': 'root', 'children': []}
        self.definitions = {}  # Store definitions by unique ID: {id: node_info}
        self.current_scope_id = None # Track the ID of the function/method being visited

    def build_structure(self):
        """Performs two passes: 1. Find definitions, 2. Find calls."""
        # Pass 1: Find definitions
        self.visit(ast.parse(self.code_content))

        # Reset scope for Pass 2
        self.current_scope_id = None
        # Pass 2: Find calls within the definitions found in Pass 1
        self.visit(ast.parse(self.code_content))

        return self.structure

    def visit_ClassDef(self, node):
        class_id = node.name
        class_source = ast.get_source_segment(self.code_content, node)
        class_node_info = {
            'name': node.name,
            'id': class_id,
            'type': 'class',
            'code': class_source,
            'start_line': node.lineno, # Add start line
            'end_line': node.end_lineno,   # Add end line
            'children': [],
            'instantiated_by': [] # New: Track where this class is instantiated
        }
        # Add to main structure BEFORE visiting children
        self.structure['children'].append(class_node_info)
        # Store definition info
        if class_id not in self.definitions: # Avoid overriding if visited multiple times? (Shouldn't happen with current logic)
             self.definitions[class_id] = class_node_info

        # Visit children (methods)
        original_scope = self.current_scope_id # Store scope in case of nested classes (though not fully handled)
        self.current_scope_id = class_id # Set scope for methods
        self.generic_visit(node)
        self.current_scope_id = original_scope # Restore scope


    def visit_FunctionDef(self, node):
        # Determine if it's a method or a top-level function
        parent = getattr(node, '_pprint_parent', None) # Simple parent check (might need improvement)
        is_method = isinstance(parent, ast.ClassDef)

        if is_method:
            scope_name = parent.name
            func_id = f"{scope_name}.{node.name}"
            func_type = 'method'
        else:
            # Assuming top-level function if parent is not ClassDef (likely Module)
            scope_name = None
            func_id = node.name
            func_type = 'function'

        # --- Pass 1 Logic: Define the node ---
        if func_id not in self.definitions:
            func_source = ast.get_source_segment(self.code_content, node)
            func_node_info = {
                'name': node.name,
                'id': func_id,
                'type': func_type,
                'code': func_source,
                'start_line': node.lineno, # Add start line
                'end_line': node.end_lineno,   # Add end line
                'calls': [],      # List of IDs this function calls
                'called_by': [],  # List of IDs that call this function
                'instantiates': [] # New: Track classes this function/method instantiates
            }
            self.definitions[func_id] = func_node_info

            # Add to the correct parent in the structure
            if is_method:
                # Find the parent class node (should exist in definitions now)
                if scope_name in self.definitions and self.definitions[scope_name]['type'] == 'class':
                    parent_class_struct = self.definitions[scope_name]
                    # Avoid adding duplicates
                    if not any(child['id'] == func_id for child in parent_class_struct.get('children', [])):
                        parent_class_struct.setdefault('children', []).append(func_node_info)
                else:
                    # Fallback: search in structure (should ideally not be needed)
                     for class_struct in self.structure['children']:
                         if class_struct['type'] == 'class' and class_struct['name'] == scope_name:
                             if not any(child['id'] == func_id for child in class_struct.get('children', [])):
                                  class_struct.setdefault('children', []).append(func_node_info)
                             break
            else:
                 # Avoid adding duplicates if visited multiple times
                 if not any(child['id'] == func_id for child in self.structure['children'] if child['type'] == 'function'):
                    self.structure['children'].append(func_node_info)

        # --- Pass 2 Logic: Find calls/instantiations within this function ---
        # Set current scope for call detection
        original_scope = self.current_scope_id
        self.current_scope_id = func_id
        # Visit the body of the function to find calls
        self.generic_visit(node)
        # Restore original scope
        self.current_scope_id = original_scope


    def visit_Call(self, node):
        # --- Pass 2 Logic: Process a call or instantiation ---
        if self.current_scope_id: # Only process if we are inside a known function/method scope
            caller_id = self.current_scope_id
            # Ensure caller exists in definitions (it should if Pass 1 worked)
            if caller_id not in self.definitions:
                self.generic_visit(node) # Continue traversal
                return

            callee_name = get_call_name(node)

            if callee_name:
                # Check if it's an instantiation of a known class
                if callee_name in self.definitions and self.definitions[callee_name]['type'] == 'class':
                    class_id = callee_name
                    # Record instantiation
                    # Add to caller's 'instantiates' list (avoid duplicates)
                    if class_id not in self.definitions[caller_id]['instantiates']:
                        self.definitions[caller_id]['instantiates'].append(class_id)

                    # Add to class's 'instantiated_by' list (avoid duplicates)
                    if caller_id not in self.definitions[class_id]['instantiated_by']:
                         self.definitions[class_id]['instantiated_by'].append(caller_id)

                else: # Otherwise, treat as a potential function/method call
                    # Attempt to find the definition ID matching the callee_name
                    # This is a simplification: it doesn't handle complex scopes or aliasing.
                    # It prioritizes methods within the same class if the caller is a method.
                    potential_callee_ids = []
                    if '.' in caller_id: # Caller is a method (Class.method)
                        caller_class = caller_id.split('.')[0]
                        potential_callee_ids.append(f"{caller_class}.{callee_name}") # Method in same class?

                    potential_callee_ids.append(callee_name) # Top-level function or method from another class?

                    found_callee_id = None
                    for p_id in potential_callee_ids:
                        # Make sure it's not a class instantiation we already handled
                        if p_id in self.definitions and self.definitions[p_id]['type'] in ['function', 'method']:
                            found_callee_id = p_id
                            break

                    if found_callee_id:
                        # Add to caller's 'calls' list (avoid duplicates)
                        if found_callee_id not in self.definitions[caller_id]['calls']:
                            self.definitions[caller_id]['calls'].append(found_callee_id)

                        # Add to callee's 'called_by' list (avoid duplicates)
                        if caller_id not in self.definitions[found_callee_id]['called_by']:
                            self.definitions[found_callee_id]['called_by'].append(caller_id)

        # Continue traversal in case of nested calls
        self.generic_visit(node)

    # Helper to assign parents for scope detection (simple version)
    def generic_visit(self, node):
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        setattr(item, '_pprint_parent', node)
                        self.visit(item)
            elif isinstance(value, ast.AST):
                setattr(value, '_pprint_parent', node)
                self.visit(value)


# Store original code content globally (simple approach for demo)
# In a real app, consider better state management (e.g., session, database)
original_code_store = {}

def analyze_code(code_content):
    """Parses Python code and returns a tree structure with call relationships."""
    try:
        # Add parent pointers for context during traversal
        tree = ast.parse(code_content)
        # Simple parent assignment (might not be robust for all cases)
        for node in ast.walk(tree):
             for child in ast.iter_child_nodes(node):
                 setattr(child, '_pprint_parent', node)

        analyzer = CodeAnalyzer(code_content)
        structure = analyzer.build_structure()
        return structure
    except SyntaxError as e:
        # Handle parsing errors gracefully
        print(f"Syntax Error during parsing: {e}")
        return {'name': 'root', 'id': 'root', 'children': [], 'error': f'Syntax Error: {e}'}
    except Exception as e:
        print(f"Error during analysis: {e}")
        return {'name': 'root', 'id': 'root', 'children': [], 'error': f'Analysis Error: {e}'}


@app.route('/', methods=['GET', 'POST'])
def index():
    code_structure = None
    error = None
    uploaded_filename = None # Keep track of the uploaded file name
    original_file_path = None # Keep track of the saved file path

    if request.method == 'POST':
        if 'file' not in request.files:
            error = 'No file part'
        else:
            file = request.files['file']
            if file.filename == '':
                error = 'No selected file'
            elif file and file.filename.endswith('.py'):
                try:
                    # Save the uploaded file to allow editing later
                    timestamp = int(time.time())
                    original_filename = file.filename
                    # Sanitize filename slightly (basic)
                    safe_filename = f"{timestamp}_{''.join(c for c in original_filename if c.isalnum() or c in ['.', '_'])}"
                    original_file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
                    file.seek(0) # Go back to the start of the file stream
                    file.save(original_file_path)
                    uploaded_filename = safe_filename # Store the name we saved it as

                    # Read content from the saved file for analysis
                    with open(original_file_path, 'r', encoding='utf-8') as f_saved:
                        code_content = f_saved.read()

                    # Store the original content for the save operation
                    original_code_store[uploaded_filename] = code_content

                    code_structure = analyze_code(code_content)
                    if 'error' in code_structure:
                        error = code_structure['error'] # Pass analysis errors

                except Exception as e:
                    error = f"Error processing file: {e}"
            else:
                error = 'Invalid file type, please upload a .py file'

    # Default structure if no file is uploaded or on GET request
    if code_structure is None and error is None:
        code_structure = {'name': 'root', 'id': 'root', 'children': []} # Provide an empty root

    return render_template('index.html',
                           code_structure_json=json.dumps(code_structure),
                           error=error,
                           uploaded_filename=uploaded_filename # Pass filename to template
                           )

@app.route('/save', methods=['POST'])
def save_code():
    data = request.get_json()
    filename = data.get('filename')
    node_id = data.get('node_id') # We might use this later if needed
    edited_code = data.get('edited_code')
    start_line = data.get('start_line')
    end_line = data.get('end_line')

    if not all([filename, edited_code, start_line is not None, end_line is not None]):
        return jsonify({'success': False, 'error': 'Missing data'}), 400

    # Retrieve the original code using the filename as key
    original_code = original_code_store.get(filename)
    if original_code is None:
         # Fallback: try reading from the originally saved file path if store is empty
         original_file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
         if os.path.exists(original_file_path):
             try:
                 with open(original_file_path, 'r', encoding='utf-8') as f:
                     original_code = f.read()
             except Exception as e:
                 return jsonify({'success': False, 'error': f'Could not read original file: {e}'}), 500
         else:
              return jsonify({'success': False, 'error': 'Original code not found or file missing.'}), 404

    try:
        lines = original_code.splitlines(True) # Keep line endings
        # Adjust to 0-based index for list slicing
        start_index = start_line - 1
        end_index = end_line # Slicing is exclusive at the end

        # Basic validation
        if start_index < 0 or end_index > len(lines) or start_index >= end_index:
             return jsonify({'success': False, 'error': 'Invalid line numbers for replacement.'}), 400

        # Construct the new code
        # Ensure edited code ends with a newline if the original block did, or if needed
        if not edited_code.endswith('\n'):
            edited_code += '\n'

        new_lines = lines[:start_index] + [edited_code] + lines[end_index:]
        modified_code = "".join(new_lines)

        # Save the modified code back to the *same file* in uploads for simplicity in this demo
        # In a real app, you might save as a new version or different file
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(modified_code)

        # Update the stored code as well
        original_code_store[filename] = modified_code

        return jsonify({'success': True, 'message': f'File {filename} saved successfully.'})

    except Exception as e:
        print(f"Error saving file: {e}") # Log error server-side
        return jsonify({'success': False, 'error': f'Error saving file: {e}'}), 500


if __name__ == '__main__':
    app.run(debug=True)
