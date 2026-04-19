"""One-time script to create a Rev.ai custom vocabulary for Buddhist terminology."""

import json
import os
import sys
import urllib.request

REVAI_TOKEN = os.environ.get("REVAI_ACCESS_TOKEN")
if not REVAI_TOKEN:
    print("Set REVAI_ACCESS_TOKEN environment variable")
    sys.exit(1)

# Buddhist/Pali/Sanskrit terminology
# Each entry can be a simple string or {"phrase": "...", "weight": N}
# Weight ranges from 1-5, default is 1. Higher = stronger bias.
PHRASES = [
    # Core concepts
    "Dharma", "Dhamma", "Sangha", "Buddha",
    "Sutta", "Sutra", "Vinaya", "Abhidhamma",
    # Meditation
    "Vipassana", "Samatha", "Jhana", "Samadhi",
    "Satipatthana", "Anapanasati", "Metta", "Karuna",
    "Mudita", "Upekkha",
    # Key teachings
    "Dukkha", "Anatta", "Anicca", "Nibbana", "Nirvana",
    "Sila", "Panna", "Prajna",
    # Practice terms
    "Dana", "Bhavana", "Sati", "Mindfulness",
    "Bodhisattva", "Tathagata", "Bhikkhu", "Bhikkhuni",
    "Sangha", "Arhat", "Arahant",
    # Common Pali phrases
    "Sadhu", "Namo Tassa", "Buddham Saranam Gacchami",
    # Path factors
    "Samma", "Right View", "Right Intention",
    "Noble Eightfold Path", "Four Noble Truths",
    "Dependent Origination", "Paticca Samuppada",
    # Hindrances and factors
    "Kilesa", "Nivarana", "Bojjhanga",
    "Khanda", "Skandha", "Vedana", "Sanna", "Sankhara", "Vinnana",
]

url = "https://api.rev.ai/speechtotext/v1/vocabularies"
headers = {
    "Authorization": f"Bearer {REVAI_TOKEN}",
    "Content-Type": "application/json",
}
data = json.dumps({
    "custom_vocabularies": [{"phrases": PHRASES}],
}).encode("utf-8")

req = urllib.request.Request(url, data=data, headers=headers, method="POST")

try:
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        print(f"Vocabulary created!")
        print(f"  ID: {result['id']}")
        print(f"  Status: {result['status']}")
        print(f"  Created: {result.get('created_on', 'N/A')}")
        print(f"\nSave this ID — you'll need it for the frontend config.")
        print(f"Check status: curl -H 'Authorization: Bearer $REVAI_ACCESS_TOKEN' "
              f"https://api.rev.ai/speechtotext/v1/vocabularies/{result['id']}")
except urllib.error.HTTPError as e:
    print(f"Error {e.code}: {e.read().decode('utf-8')}")
    sys.exit(1)
