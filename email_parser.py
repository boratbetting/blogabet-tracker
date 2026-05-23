#!/usr/bin/env python3
"""
Blogabet Email Parser — Gmail IMAP
Reads Blogabet notification emails, parses picks, classifies volleyball.
Outputs: data/email_signals.json (rolling 500 signals)

Environment vars:
  GMAIL_USER — Gmail address
  GMAIL_APP_PASSWORD — App password (NOT regular password)
"""

import imaplib
import email
from email.header import decode_header
import json
import os
import re
import hashlib
from datetime import datetime, timedelta

# ── Config ──
GMAIL_USER = os.environ.get('GMAIL_USER', '')
GMAIL_PASS = os.environ.get('GMAIL_APP_PASSWORD', '')
IMAP_HOST = 'imap.gmail.com'
IMAP_PORT = 993
SENDER_FILTER = 'blogabet.com'
MAX_SIGNALS = 500
OUTPUT_FILE = 'data/email_signals.json'
DAYS_BACK = 14  # scan last 14 days

# ── Volleyball keywords ──
VOLLEYBALL_KEYWORDS = [
    'volleyball', 'volley', 'siatkówka', 'siatkowka',
    'plusliga', 'superliga', 'serie a1', 'superlega', 'bundesliga',
    'ligue a', 'eredivisie', 'mestaruusliiga', 'liga siatkowki',
    'cev', 'fivb', 'vnl', 'nations league',
    'set handicap', 'sets', 'handicap (sets)',
    'game lines', 'total points', 'match total',
    '3-0', '3-1', '3-2', '2-3', '1-3', '0-3',
    'efeler', 'lega volley', 'volei', 'pallavolo',
    'tauron', 'pge', 'jastrzebski', 'zaksa', 'resovia',
    'modena', 'perugia', 'trento', 'trentino', 'civitanova',
    'berlin volleys', 'friedrichshafen', 'luneburg',
    'tours', 'montpellier', 'chaumont', 'poitiers',
    'knack', 'maaseik', 'roeselare',
    'conegliano', 'novara', 'busto', 'scandicci',
    'dresdner', 'schwerin', 'stuttgart', 'munster',
    'chemik police', 'bielsko', 'rzeszow', 'lodz',
]

VOLLEYBALL_LEAGUES_PATTERN = re.compile(
    r'volley|siatkow|pluslig|superlega|serie a1|bundeslig|ligue a|'
    r'eredivisi|mestaruus|cev |fivb|nations league|vnl|'
    r'pallavol|volei|efeler|tauron|lega vol',
    re.IGNORECASE
)


def is_volleyball(text):
    """Check if pick is volleyball-related"""
    text_lower = text.lower()
    # Direct sport label
    if re.search(r'\bvolleyball\b', text_lower):
        return True
    # League/keyword match
    for kw in VOLLEYBALL_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    if VOLLEYBALL_LEAGUES_PATTERN.search(text):
        return True
    return False


def parse_blogabet_email(msg):
    """Parse a Blogabet notification email into a signal dict"""
    # Get subject
    subject = ''
    raw_subject = msg.get('Subject', '')
    if raw_subject:
        decoded = decode_header(raw_subject)
        subject = ''.join(
            part.decode(enc or 'utf-8') if isinstance(part, bytes) else str(part)
            for part, enc in decoded
        )

    # Get date
    date_str = msg.get('Date', '')
    try:
        from email.utils import parsedate_to_datetime
        msg_date = parsedate_to_datetime(date_str)
    except Exception:
        msg_date = datetime.utcnow()

    # Get body (plain text preferred)
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    body = payload.decode(charset, errors='replace')
                    break
            elif ct == 'text/html' and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    body = payload.decode(charset, errors='replace')
                    # Strip HTML tags
                    body = re.sub(r'<[^>]+>', ' ', body)
                    body = re.sub(r'\s+', ' ', body).strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            body = payload.decode(charset, errors='replace')

    full_text = subject + '\n' + body

    # ── Parse pick details ──
    signal = {
        'id': '',
        'timestamp': msg_date.isoformat(),
        'tipster': '',
        'tipster_url': '',
        'match': '',
        'home': '',
        'away': '',
        'pick': '',
        'odds': 0,
        'stake': 0,
        'sport': '',
        'league': '',
        'is_volleyball': False,
        'is_live': False,
        'kick_off': '',
        'bookmaker': '',
        'raw_subject': subject,
        'pick_url': '',
    }

    # Tipster name — "XYZ published a new pick"
    m = re.search(r'(?:^|\n)\s*(.+?)\s*published a new pick', full_text)
    if m:
        signal['tipster'] = m.group(1).strip().lstrip('* +')

    # Tipster URL — blogabet.com link
    m = re.search(r'(https?://\S+\.blogabet\.com\S*)', full_text)
    if m:
        signal['tipster_url'] = m.group(1).strip('()')

    # Match — "Team A - Team B" or "Team A vs Team B"
    m = re.search(r'\n\s*([A-Z][\w\s\.\(\)]+?)\s*[-–]\s*([A-Z][\w\s\.\(\)]+?)\s*\n', full_text)
    if m:
        signal['home'] = m.group(1).strip()
        signal['away'] = m.group(2).strip()
        signal['match'] = f"{signal['home']} vs {signal['away']}"

    # Pick line — "Handicap (Sets) Team +1.5 @ 1.833"
    m = re.search(r'((?:Handicap|Over|Under|Match Winner|Moneyline|Total|1X2|ML|Set)\s*(?:\([^)]*\))?\s*[^\n@]+?)\s*@\s*([\d\.]+)', full_text, re.IGNORECASE)
    if m:
        signal['pick'] = m.group(1).strip()
        try:
            signal['odds'] = float(m.group(2))
        except ValueError:
            pass

    # Stake — "2/10" or "5/10" etc.
    m = re.search(r'(\d+)/10', full_text)
    if m:
        signal['stake'] = int(m.group(1))

    # Sport — "Volleyball / Livebet" or "Football / ..."
    m = re.search(r'\b(Volleyball|Football|Basketball|Tennis|Hockey|Baseball|Handball|Esports|Cricket|Boxing|MMA)\b', full_text, re.IGNORECASE)
    if m:
        signal['sport'] = m.group(1).capitalize()

    # League
    m = re.search(r'(?:Volleyball|Football|Basketball|Tennis)\s*/\s*(.+?)(?:\s*/|\s*Odds|\s*Kick)', full_text, re.IGNORECASE)
    if m:
        signal['league'] = m.group(1).strip()

    # Live bet
    if re.search(r'\bLIVE\b|\bLivebet\b|\bin-play\b', full_text, re.IGNORECASE):
        signal['is_live'] = True

    # Kick off time
    m = re.search(r'Kick off:\s*(.+?)(?:\s*Odds|\s*$)', full_text, re.IGNORECASE)
    if m:
        signal['kick_off'] = m.group(1).strip()

    # Bookmaker — [bet365] or (bet365)
    m = re.search(r'\[(bet365|pinnacle|unibet|1xbet|betfair|bwin|william hill|betway)\]', full_text, re.IGNORECASE)
    if m:
        signal['bookmaker'] = m.group(1)
    else:
        m = re.search(r'\b(bet365|pinnacle|unibet|1xbet|sbobet|dafabet|betfair|marathon)\b', full_text, re.IGNORECASE)
        if m:
            signal['bookmaker'] = m.group(1)

    # Pick URL — "View pick" link
    m = re.search(r'(https?://\S+blogabet\.com/pick/\S+)', full_text)
    if m:
        signal['pick_url'] = m.group(1).strip('()')

    # Volleyball classification
    signal['is_volleyball'] = is_volleyball(full_text)
    if signal['sport'].lower() == 'volleyball':
        signal['is_volleyball'] = True

    # Generate unique ID
    id_source = f"{signal['tipster']}_{signal['match']}_{signal['timestamp']}"
    signal['id'] = hashlib.md5(id_source.encode()).hexdigest()[:12]

    return signal


def fetch_signals():
    """Connect to Gmail IMAP and fetch Blogabet notification emails"""
    if not GMAIL_USER or not GMAIL_PASS:
        print("❌ GMAIL_USER or GMAIL_APP_PASSWORD not set")
        return []

    print(f"📡 Connecting to Gmail IMAP as {GMAIL_USER}")
    
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(GMAIL_USER, GMAIL_PASS)
        print("✅ Login successful")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return []

    mail.select('INBOX')

    # Search for Blogabet emails from last N days
    since_date = (datetime.utcnow() - timedelta(days=DAYS_BACK)).strftime('%d-%b-%Y')
    search_query = f'(FROM "blogabet" SINCE {since_date})'
    
    print(f"🔍 Searching: {search_query}")
    status, data = mail.search(None, search_query)
    
    if status != 'OK':
        print(f"❌ Search failed: {status}")
        mail.logout()
        return []

    msg_ids = data[0].split()
    print(f"📬 Found {len(msg_ids)} emails from Blogabet (last {DAYS_BACK} days)")

    signals = []
    for mid in msg_ids:
        try:
            status, msg_data = mail.fetch(mid, '(RFC822)')
            if status != 'OK':
                continue
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            signal = parse_blogabet_email(msg)
            
            # Only keep signals with at least tipster + pick
            if signal['tipster'] or signal['pick'] or signal['match']:
                signals.append(signal)
                
        except Exception as e:
            print(f"  ⚠ Error parsing email {mid}: {e}")
            continue

    mail.logout()
    print(f"✅ Parsed {len(signals)} signals")
    
    return signals


def merge_and_save(new_signals):
    """Merge with existing signals, dedupe, save"""
    existing = []
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE) as f:
                existing = json.load(f)
            print(f"📦 Existing signals: {len(existing)}")
        except Exception:
            pass

    # Merge: existing + new, dedupe by id
    seen_ids = set()
    merged = []
    for s in new_signals + existing:
        if s.get('id') and s['id'] not in seen_ids:
            seen_ids.add(s['id'])
            merged.append(s)

    # Sort by timestamp desc
    merged.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

    # Keep rolling window
    merged = merged[:MAX_SIGNALS]

    # Stats
    volleyball = sum(1 for s in merged if s.get('is_volleyball'))
    live = sum(1 for s in merged if s.get('is_live'))
    
    print(f"\n{'='*50}")
    print(f"📊 Total signals: {len(merged)}")
    print(f"🏐 Volleyball: {volleyball}")
    print(f"⚡ Live: {live}")
    print(f"📅 Date range: {merged[-1]['timestamp'][:10] if merged else '?'} → {merged[0]['timestamp'][:10] if merged else '?'}")
    
    # Ensure data dir exists
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    
    print(f"💾 Saved: {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE) // 1024}KB)")
    
    return merged


if __name__ == '__main__':
    print("=" * 50)
    print("  BLOGABET EMAIL PARSER")
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)
    
    signals = fetch_signals()
    if signals:
        merge_and_save(signals)
    else:
        print("\n⚠ No new signals found")
        # Still create empty file if it doesn't exist
        if not os.path.exists(OUTPUT_FILE):
            os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
            with open(OUTPUT_FILE, 'w') as f:
                json.dump([], f)
            print(f"💾 Created empty: {OUTPUT_FILE}")
