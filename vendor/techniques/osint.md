# OSINT Techniques

## WHOIS / domain lookup
**When:** Domain or IP provided; need registrant, nameserver, or history.
**Tools:** `whois`, `dig`, `nslookup`, `viewdns.info` (passive)
**Caveats:** Privacy-protected registrars; use historical WHOIS (DomainTools) for changes.

## reverse image search
**When:** Image provided; need origin, person, or location.
**Tools:** Google Lens, TinEye, Yandex Images; Exiftool for GPS/device metadata.
**Caveats:** Cropped or compressed images may not match; try grayscale or partial crop.

## social media / username enumeration
**When:** Username or handle given; need to find linked accounts.
**Tools:** `sherlock`, `whatsmyname`, manual search of GitHub/Twitter/LinkedIn.
**Caveats:** Common usernames have false positives; verify content matches challenge context.

## geolocation from photo
**When:** Photo with environmental clues (signs, architecture, shadows, vegetation).
**Tools:** GeoGuessr logic; Google Street View; sun angle tools for time estimation.
**Caveats:** Shadows give approximate latitude; architecture and road signs narrow region.

## certificate transparency
**When:** Domain given; need subdomains or related infrastructure.
**Tools:** `crt.sh`, `censys.io`, `certstream`
**Caveats:** CT logs may lag by hours; expired certs still appear.

## web archive / cache
**When:** Page is down or content changed; need historical version.
**Tools:** `web.archive.org`, Google cache (`cache:url`), `cachedview.nl`
**Caveats:** Wayback Machine has gaps; JS-heavy pages may not render correctly.
