from flask import Flask, render_template, request
import ast
import json
import inspect # Used for getting source, though ast.get_source_segment is often better with AST

app = Flask(__name__)

def analyze_code(code_content):
    """Parses Python code and returns a tree structure."""
    tree = ast.parse(code_content)
    structure = {'name': 'root', 'children': []}
    lines = code_content.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_source = ast.get_source_segment(code_content, node)
            class_node = {'name': node.name, 'type': 'class', 'code': class_source, 'children': []}
            for sub_node in ast.walk(node):
                if isinstance(sub_node, ast.FunctionDef) and sub_node in node.body:
                     # Ensure it's a method directly under the class, not nested further
                    method_source = ast.get_source_segment(code_content, sub_node)
                    method_node = {'name': sub_node.name, 'type': 'method', 'code': method_source}
                    class_node['children'].append(method_node)
            structure['children'].append(class_node)
        elif isinstance(node, ast.FunctionDef) and isinstance(node.__dict__.get('_pprint_parent'), ast.Module):
             # Top-level functions (parent is the Module)
             # Check if it's already part of a class to avoid duplicates (simple check)
             is_method = False
             for class_struct in structure['children']:
                 if class_struct['type'] == 'class':
                     for method in class_struct.get('children', []):
                         if method['name'] == node.name:
                             is_method = True
                             break
                 if is_method:
                     break
             if not is_method:
                func_source = ast.get_source_segment(code_content, node)
                func_node = {'name': node.name, 'type': 'function', 'code': func_source}
                structure['children'].append(func_node)

    # Parent assignment might not be strictly needed for this structure anymore
    # but doesn't hurt if left for potential future use.
    # for node in ast.walk(tree):
    #     for child in ast.iter_child_nodes(node):
    #         child._pprint_parent = node

    return structure

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