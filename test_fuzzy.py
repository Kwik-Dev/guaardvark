import difflib

def _find_fuzzy_match(content: str, old_text: str, threshold: float = 0.85):
    content_lines = content.splitlines()
    old_lines = old_text.splitlines()
    
    if not old_lines or not content_lines:
        return None
        
    best_match = None
    best_ratio = 0.0
    match_count = 0
    
    # Try window sizes around the length of old_text
    target_len = len(old_lines)
    window_sizes = [target_len - 1, target_len, target_len + 1, target_len + 2]
    window_sizes = [w for w in window_sizes if w > 0]
    
    matches = []
    
    for w in window_sizes:
        for i in range(len(content_lines) - w + 1):
            window_text = "\n".join(content_lines[i:i+w])
            ratio = difflib.SequenceMatcher(None, window_text, old_text).ratio()
            if ratio >= threshold:
                matches.append((ratio, window_text))
                
    if not matches:
        return None
        
    # Find the max ratio
    max_ratio = max(m[0] for m in matches)
    # Filter matches that are very close to max_ratio
    best_matches = set(m[1] for m in matches if max_ratio - m[0] < 0.01)
    
    if len(best_matches) > 1:
        # Not unique
        return None
        
    return best_matches.pop()

print("Fuzzy result:")
print(_find_fuzzy_match("def foo():\n    print('hello')\n    return 1", "def foo():\n    print('hell')"))

