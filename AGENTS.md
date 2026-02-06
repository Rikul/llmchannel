# Agent Guidelines for LLM Channel

IRC-style chat application with LLM bot integration built on Flask.

## Build & Run Commands

```bash
# Setup
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run development server
python app.py
# Server starts on http://localhost:5000

# No existing test suite - create tests in tests/ directory if needed
```

## Code Style Guidelines

### Python

**Formatting:**
- 4-space indentation
- Double quotes for strings
- Max line length: 100 characters
- Two blank lines before top-level functions/classes
- One blank line between methods

**Naming:**
- `snake_case` for functions, variables, module names
- `PascalCase` for class names
- `UPPER_CASE` for module-level constants
- Private methods prefix with underscore: `_helper()`

**Imports:**
```python
# 1. Standard library
import copy
import threading
import uuid

# 2. Third-party
from flask import Flask

# 3. Local modules (use absolute imports)
from config import OPERATOR_NAME
from message_store import MessageStore
```

**Documentation:**
- Use `# ── Section Name ──` headers to organize code sections
- Docstrings for public functions (Google style)
```python
def stream_response(provider, model, api_key, messages, system_prompt):
    """Generator that yields tokens one at a time from the LLM API."""
```

### JavaScript

**Formatting:**
- 4-space indentation
- Single quotes for strings
- Semicolons required
- Max line length: 100 characters

**Naming:**
- `camelCase` for functions and variables
- `PascalCase` for module namespaces
- Prefix jQuery objects with `$`: `$card`, `$list`

**Patterns:**
- Use IIFE module pattern for namespacing:
```javascript
var ModuleName = (function () {
    function privateFn() {}
    return { publicFn: privateFn };
})();
```

**Event Handling:**
- Use jQuery's `.on()` for event binding
- Namespace events for easy removal

## Architecture Patterns

**Flask Routes:**
- Use decorators for HTTP methods
- Return JSON responses with appropriate status codes
- Handle errors with descriptive messages

**Thread Safety:**
- Use `threading.Lock()` for shared state
- Keep locks brief - release ASAP
- Copy data when passing between threads

**Error Handling:**
- Python: Catch exceptions and return/yield user-friendly messages
- JavaScript: Check for null/undefined before accessing properties

**API Design:**
- RESTful endpoints under `/api/`
- GET for retrieval, POST/PUT for mutations, DELETE for removal
- Mask sensitive data (API keys) in responses

## Frontend Conventions

**CSS:**
- Use CSS custom properties for theming
- BEM-like naming: `.bot-card`, `.toggle-switch`
- Inline styles only for dynamic values

**HTML Templates:**
- Jinja2 templating
- Keep logic minimal in templates

## Security

- Never commit API keys or secrets
- Mask API keys in API responses: `"****" + key[-4:]`
- Validate all user input
- Use parameterized data structures (no SQL injection risk with current design)

## LLM Client Integration

When adding new providers:
1. Add to `llm_clients.py`
2. Update `modelHints` in `settings.js`
3. Follow existing streaming generator pattern
4. Handle provider-specific requirements (e.g., Anthropic requires user role first)
