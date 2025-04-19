from flask import Flask, render_template, request
import ast
import json
# import inspect # Not strictly needed with ast.get_source_segment

app = Flask(__name__)

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
            'children': []
        }
        # Add to main structure BEFORE visiting children to handle methods correctly
        self.structure['children'].append(class_node_info)
        # Store definition info (though classes themselves aren't directly callable in this context)
        # self.definitions[class_id] = class_node_info # Optional

        # Visit children (methods)
        self.generic_visit(node)


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
                'calls': [],      # List of IDs this function calls
                'called_by': [],  # List of IDs that call this function
            }
            self.definitions[func_id] = func_node_info

            # Add to the correct parent in the structure
            if is_method:
                # Find the parent class node in the main structure
                for class_struct in self.structure['children']:
                    if class_struct['type'] == 'class' and class_struct['name'] == scope_name:
                        # Avoid adding duplicates if visited multiple times
                        if not any(child['id'] == func_id for child in class_struct.get('children', [])):
                             class_struct.setdefault('children', []).append(func_node_info)
                        break
            else:
                 # Avoid adding duplicates if visited multiple times
                 if not any(child['id'] == func_id for child in self.structure['children'] if child['type'] == 'function'):
                    self.structure['children'].append(func_node_info)

        # --- Pass 2 Logic: Find calls within this function ---
        # Set current scope for call detection
        original_scope = self.current_scope_id
        self.current_scope_id = func_id
        # Visit the body of the function to find calls
        self.generic_visit(node)
        # Restore original scope
        self.current_scope_id = original_scope


    def visit_Call(self, node):
        # --- Pass 2 Logic: Process a call ---
        if self.current_scope_id: # Only process calls if we are inside a known function/method scope
            caller_id = self.current_scope_id
            callee_name = get_call_name(node)

            if callee_name:
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
                    if p_id in self.definitions:
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
    if request.method == 'POST':
        if 'file' not in request.files:
            error = 'No file part'
        else:
            file = request.files['file']
            if file.filename == '':
                error = 'No selected file'
            elif file and file.filename.endswith('.py'):
                try:
                    code_content = file.read().decode('utf-8')
                    code_structure = analyze_code(code_content)
                except Exception as e:
                    error = f"Error parsing file: {e}"
            else:
                error = 'Invalid file type, please upload a .py file'

    # Default structure if no file is uploaded or on GET request
    if code_structure is None and error is None:
        code_structure = {'name': 'root', 'children': []} # Provide an empty root

    return render_template('index.html', code_structure_json=json.dumps(code_structure), error=error)

if __name__ == '__main__':
    app.run(debug=True)
