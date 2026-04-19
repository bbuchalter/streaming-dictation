"""One-time script to create a Rev.ai custom vocabulary for Buddhist terminology."""

import json
import os
import sys
import urllib.request

REVAI_TOKEN = os.environ.get("REVAI_ACCESS_TOKEN")
if not REVAI_TOKEN:
    print("Set REVAI_ACCESS_TOKEN environment variable")
    sys.exit(1)

# Buddhist/Pali/Sanskrit terminology + Plum Village / Thich Nhat Hanh vocabulary
# Each entry can be a simple string or {"phrase": "...", "weight": N}
# Weight ranges from 1-5, default is 1. Higher = stronger bias.
#
# Focus: terms that speech-to-text engines would likely get WRONG, especially
# non-English words, proper nouns, and specialized phrases.

PHRASES = [
    # =========================================================================
    # NAMES AND HONORIFICS
    # =========================================================================

    # Thich Nhat Hanh and his titles
    "Thich Nhat Hanh",
    "Thay",                      # common name for TNH (Vietnamese: teacher)
    "Thay Nhat Hanh",

    # Vietnamese Buddhist honorifics
    "Thich",                     # Vietnamese monastic surname (from Sakya)
    "Su Ong",                    # grandfather teacher (TNH's honorific)
    "Su Co",                     # sister / nun
    "Su Chu",                    # young monk
    "Su Ba",                     # elder nun

    # Sister Chan Khong (TNH's first ordained disciple)
    "Chan Khong",
    "Sister Chan Khong",
    "Cao Ngoc Phuong",           # her birth name

    # Senior Plum Village Dharma Teachers (monastics)
    "Brother Phap Huu",
    "Chan Phap Huu",
    "Brother Phap Linh",
    "Chan Phap Linh",
    "Brother Phap Dung",
    "Chan Phap Dung",
    "Brother Phap Lai",
    "Chan Phap Lai",
    "Brother Phap Lieu",
    "Chan Phap Lieu",
    "Brother Phap Xa",
    "Chan Phap Xa",
    "Brother Phap Ly",
    "Chan Phap Ly",
    "Brother Phap Ung",
    "Chan Phap Ung",
    "Sister Chan Duc",
    "Sister Chan Dieu Nghiem",
    "Sister Chan Tu Nghiem",
    "Sister Chan Thoai Nghiem",
    "Sister Chan Dinh Nghiem",
    "Sister Chan Tue Nghiem",
    "Sister Chan Giac Nghiem",
    "Sister Chan Hoi Nghiem",
    "Sister Chan Lang Nghiem",
    "Sister Chan Dao Nghiem",
    "Sister Chan Thuan Nghiem",
    "Sister Chan Phung Nghiem",
    "Sister Chan Phuong Nghiem",
    "Sister Chan Hien Nghiem",
    "Sister Chan Si Nghiem",
    "Sister Chan Tai Nghiem",
    "Sister Chan Sang Nghiem",
    "Sister Chan Trai Nghiem",
    "Brother Chan Troi Bao Tang",
    "Sister Chan Trang Mai Thon",
    "Brother Chan Troi Pham Hanh",
    "Sister Chan Trang Tam Muoi",

    # Dharma name prefixes (used for all PV monastics)
    "Chan",                      # True (Dharma name prefix)
    "Phap",                      # Dharma (brother prefix)
    "Nghiem",                    # Adornment (sister suffix)
    "Troi",                      # Sky (newer brother prefix)
    "Trang",                     # Adornment (newer sister prefix)

    # TNH's lineage teachers and historical figures
    "Chan That",                 # TNH's teacher's dharma title
    "Thanh Quy",                 # TNH's teacher's lineage name
    "Tue Minh",                  # TNH's grand-teacher
    "Thanh Thai",                # lineage name of Tue Minh

    # TNH's own ordination names
    "Trung Quang",               # TNH's lineage name (Calm Light)
    "Phung Xuan",                # TNH's monastic dharma name (Meeting Spring)
    "Nhat Chi Mai",              # self-immolated peace activist, TNH associate

    # Historical Buddhist figures TNH references
    "Nagarjuna",
    "Vasubandhu",
    "Sthiramati",
    "Xuanzang",
    "Huyen Trang",               # Vietnamese for Xuanzang
    "Fazang",
    "Fa Zang",
    "Huineng",
    "Linji",
    "Taixu",
    "Shakyamuni",

    # =========================================================================
    # PLACES - Monasteries and Practice Centers
    # =========================================================================

    # Plum Village France
    "Plum Village",
    "Lang Mai",                  # Vietnamese for Plum Village
    "Upper Hamlet",
    "Lower Hamlet",
    "New Hamlet",
    "Xom Thuong",                # Vietnamese: Upper Hamlet
    "Xom Ha",                    # Vietnamese: Lower Hamlet
    "Xom Moi",                   # Vietnamese: New Hamlet

    # Temple names within Plum Village
    "Dharma Cloud Temple",
    "Phap Van",                  # Vietnamese: Dharma Cloud
    "Dharma Nectar Temple",
    "Cam Lo",                    # Vietnamese: Dharma Nectar
    "Lovingkindness Temple",
    "Son Ha",                    # Son Ha Temple
    "Son Ha Temple",

    # Vietnam
    "Tu Hieu",                   # TNH's root temple in Hue
    "Tu Hieu Pagoda",
    "Bat Nha",                   # Prajna Monastery
    "Prajna Temple",
    "Hue",                       # city in Vietnam

    # US Monasteries
    "Blue Cliff Monastery",
    "Deer Park Monastery",
    "Loc Uyen",                  # Vietnamese for Deer Park
    "Magnolia Grove Monastery",
    "Magnolia Grove",

    # European Centers
    "EIAB",                      # European Institute of Applied Buddhism
    "European Institute of Applied Buddhism",
    "Waldbroel",                 # EIAB location in Germany
    "Healing Spring Monastery",
    "La Source Guerissante",     # French: Healing Spring
    "Maison de l'Inspir",

    # Asia-Pacific Centers
    "Thai Plum Village",
    "AIAB",                      # Asian Institute of Applied Buddhism
    "Lotus Pond Temple",         # AIAB in Hong Kong
    "Stream Entering Monastery",
    "Nhap Luu",                  # Vietnamese: Stream Entering
    "Mountain Spring Monastery",

    # Historical locations
    "Sweet Potatoes Meditation Centre",
    "Fontvannes",                # early retreat center in France

    # =========================================================================
    # VIETNAMESE BUDDHIST TERMS
    # =========================================================================

    "Thien",                     # Vietnamese Zen
    "Lam Te",                    # Vietnamese for Linji school
    "Lam Te Thien",              # Linji Zen in Vietnamese
    "Lieu Quan",                 # sub-lineage within Lam Te
    "Nguyen Dinh",               # TNH's family lineage
    "Thien Vien",                # meditation center
    "Chua",                      # pagoda / temple
    "Tu Vien",                   # monastery
    "Xom",                       # hamlet

    # =========================================================================
    # THICH NHAT HANH'S COINED CONCEPTS AND KEY TEACHINGS
    # =========================================================================

    "Interbeing",
    "Engaged Buddhism",
    "Five Mindfulness Trainings",
    "Fourteen Mindfulness Trainings",
    "Three Doors of Liberation",
    "Plum Village Dharma Seals",
    "Forty Tenets of Plum Village",
    "Manifestation Only",        # TNH's preferred term over "Consciousness Only"
    "Fourfold Sangha",

    # =========================================================================
    # SANSKRIT AND PALI TERMS (Mahayana emphasis, as used by TNH)
    # =========================================================================

    # Core philosophical terms
    "Dharma", "Dhamma",
    "Sunyata",                   # Emptiness
    "Prajnaparamita",            # Perfection of Wisdom
    "Prajna",                    # Wisdom
    "Bodhichitta",               # Awakened mind / Mind of Love
    "Bodhicitta",                # alternate spelling
    "Tathagata",                 # Thus Come One / Thus Gone One
    "Tathagatagarbha",           # Buddha-nature
    "Tathata",                   # Suchness
    "Dharmadhatu",               # Realm of all dharmas
    "Nirvana", "Nibbana",
    "Samsara",
    "Pratityasamutpada",         # Dependent Co-arising
    "Paticca Samuppada",         # Pali: Dependent Origination
    "Anatta",                    # Non-self
    "Anicca",                    # Impermanence
    "Dukkha",                    # Suffering
    "Karma",
    "Sila",                      # Ethics / Morality
    "Samadhi",                   # Concentration

    # Three Bodies of Buddha
    "Dharmakaya",                # Body of Dharma / ultimate reality
    "Sambhogakaya",              # Body of Enjoyment
    "Nirmanakaya",               # Transformation body
    "Sanghakaya",                # Body of the Sangha (TNH term)

    # Consciousness terms (Yogacara / Manifestation Only)
    "Alaya",                     # Store consciousness
    "Alaya Vijnana",             # Store consciousness (full term)
    "Manas",                     # Mental consciousness that grasps self
    "Bija",                      # Seeds (in store consciousness)
    "Vijnana",                   # Consciousness
    "Vijnaptimatrata",           # Manifestation Only / Consciousness Only
    "Mulavijnana",               # Root consciousness
    "Sarvabijaka",               # Totality of seeds

    # Bodhisattva names
    "Avalokiteshvara",           # Bodhisattva of Compassion
    "Avalokitesvara",            # alternate transliteration
    "Manjushri",                 # Bodhisattva of Understanding
    "Samantabhadra",             # Bodhisattva of Great Action
    "Kshitigarbha",              # Earth Store Bodhisattva
    "Ksitigarbha",               # alternate spelling
    "Sadaparibhuta",             # Bodhisattva Never Despising
    "Maitreya",                  # Future Buddha / Buddha of Love
    "Mahasattva",                # Great Being
    "Akshayamati",               # Bodhisattva of Infinite Thought

    # Other Sanskrit/Pali terms used by TNH
    "Bhikkhu", "Bhikshu",       # Fully ordained monk
    "Bhikkhuni", "Bhikshuni",   # Fully ordained nun
    "Upasaka",                   # Layman
    "Upasika",                   # Laywoman
    "Arhat", "Arahant",
    "Brahmavihara",              # Four Divine Abodes
    "Metta", "Maitri",          # Loving-kindness
    "Karuna",                    # Compassion
    "Mudita",                    # Sympathetic joy
    "Upekkha", "Upeksha",       # Equanimity
    "Kalpa",                     # Eon / long time period
    "Naga",                      # Water deity / dragon
    "Deva",                      # Celestial being
    "Asura",                     # Fighting spirit
    "Preta",                     # Hungry ghost
    "Mara",                      # Tempter / obstacle
    "Cakravartin",               # Universal monarch
    "Mudra",                     # Sacred gesture
    "Indra",                     # in Indra's Net
    "Jambudvipa",                # Ancient India continent
    "Sukhavati",                 # Pure Land
    "Amitabha",                  # Buddha of Infinite Light
    "Bhumi",                     # Bodhisattva stage
    "Tripitaka",                 # Three Baskets of scripture
    "Namo",                      # Homage
    "Svaha",                     # exclamation in mantras
    "Bodhi",                     # Awakening
    "Ashrava",                   # Phenomena with leaks
    "Anashrava",                 # Phenomena without leaks
    "Nidana",                    # Link (in twelve links)
    "Avidya",                    # Ignorance
    "Tanha",                     # Craving

    # Meditation terms
    "Vipassana",                 # Insight meditation
    "Samatha",                   # Calm / tranquility meditation
    "Jhana",                     # Meditative absorption
    "Satipatthana",              # Four Establishments of Mindfulness
    "Anapanasati",               # Mindful breathing
    "Shikantaza",                # Just sitting
    "Zazen",                     # Sitting meditation (Zen)
    "Koan",                      # Zen meditation puzzle
    "Hua Tou",                   # meditation technique (before the word)
    "Sesshin",                   # Intensive retreat period
    "Samu",                      # Working meditation
    "Oryoki",                    # Formal eating practice

    # Five Aggregates / Skandhas
    "Skandha",                   # Aggregate
    "Khanda",                    # Pali: Aggregate
    "Vedana",                    # Feelings
    "Sanna",                     # Perception (Pali)
    "Sankhara",                  # Mental formations (Pali)
    "Vinnana",                   # Consciousness (Pali)

    # =========================================================================
    # SUTRA AND DISCOURSE NAMES
    # =========================================================================

    # Heart Sutra
    "Heart Sutra",
    "Prajnaparamita Heart Sutra",
    "The Insight that Brings Us to the Other Shore",

    # Diamond Sutra
    "Diamond Sutra",
    "The Diamond That Cuts Through Illusion",

    # Lotus Sutra
    "Lotus Sutra",

    # Avatamsaka Sutra
    "Avatamsaka Sutra",

    # Breathing and Mindfulness Sutras
    "Sutra on the Full Awareness of Breathing",
    "Anapanasati Sutta",
    "Sutra on the Four Establishments of Mindfulness",
    "Satipatthana Sutta",
    "Bhaddekaratta Sutta",
    "Discourse on Knowing the Better Way to Live Alone",

    # Other key sutras/discourses TNH teaches
    "Discourse on Love",
    "Discourse on Happiness",
    "Discourse on Turning the Wheel of the Dharma",
    "Dhammacakkappavattana Sutta",
    "Discourse on the Middle Way",
    "Discourse on Taking Refuge in Oneself",
    "Lankavatara Sutra",
    "Vimalakirti Sutra",
    "Platform Sutra",
    "Amitabha Sutra",
    "Sutra on the Eight Realizations of Great Beings",

    # Vasubandhu's works (central to TNH's consciousness teachings)
    "Thirty Verses on Consciousness",
    "Twenty Verses on Consciousness",
    "Fifty Verses on the Nature of Consciousness",

    # Mantras and chant phrases
    "Gate gate paragate parasamgate bodhi svaha",
    "Namo Avalokiteshvaraya",
    "Namo Avalokiteshvara",

    # =========================================================================
    # PLUM VILLAGE PRACTICE-SPECIFIC TERMS
    # =========================================================================

    # Core practices
    "Gatha",                     # Short mindfulness poem
    "Gathas",                    # plural
    "Touching the Earth",
    "Earth Touchings",
    "Beginning Anew",
    "Dharma sharing",
    "Dharma talk",
    "Dharma rain",
    "Dharma teacher",
    "Dharmacharya",              # Dharma teacher (Sanskrit)
    "Dharmacharyya",

    # Ceremony and retreat terms
    "Lamp Transmission",
    "Dharma Lamp",
    "Insight Gatha",
    "Transmission Gatha",
    "Rains Retreat",
    "Great Ordination Ceremony",
    "Face to Face Ceremony",
    "Five Precepts Transmission",
    "Noble Silence",
    "Lazy Day",

    # Daily practice terms
    "Bell of Mindfulness",
    "Inviting the Bell",
    "Hugging Meditation",
    "Tea Meditation",
    "Pebble Meditation",
    "Tangerine Meditation",
    "Walking Meditation",
    "Sitting Meditation",
    "Mindful Movements",
    "Deep Relaxation",
    "Service Meditation",
    "Flower Watering",           # appreciation part of Beginning Anew
    "Sangha building",
    "Dharma Seal",
    "Three Dharma Seals",

    # Chant names
    "The Great Bell Chant",
    "The Morning Chant",
    "Praising the Buddha",
    "From the Depths of Understanding",
    "The Sound of the Rising Tide",
    "May the Day Be Well",
    "The Three Refuges",
    "Invoking the Bodhisattvas Names",
    "Chanting from the Heart",

    # Books by TNH (frequently referenced)
    "The Heart of the Buddha's Teaching",
    "The Heart of Understanding",
    "Transformation at the Base",
    "Understanding Our Mind",
    "Breathe You Are Alive",
    "The Other Shore",
    "Peaceful Action Open Heart",
    "Finding Our True Home",
    "Opening the Heart of the Cosmos",
    "Beyond the Self",
    "Vietnam Lotus in a Sea of Fire",

    # =========================================================================
    # COMMUNITY AND ORGANIZATIONAL TERMS
    # =========================================================================

    "OI member",
    "Plum Village Community of Engaged Buddhism",
    "Unified Buddhist Church",
    "Wake Up",                   # young practitioners movement
    "Wake Up Schools",
    "ARISE",                     # Awakening through Race, Intersectionality, and Social Equity
    "ARISE Sangha",
    "Earth Holder",
    "Earth Holder Sangha",
    "Parallax Press",            # TNH's publisher
    "The Mindfulness Bell",      # Plum Village magazine
    "Thich Nhat Hanh Foundation",
    "School of Youth for Social Service",
    "SYSS",

    # =========================================================================
    # COMMON PALI PHRASES AND PATH FACTORS
    # =========================================================================

    "Sadhu",
    "Namo Tassa",
    "Buddham Saranam Gacchami",
    "Samma",                     # Right (as in Right View)
    "Noble Eightfold Path",
    "Four Noble Truths",
    "Bojjhanga",                 # Seven Factors of Enlightenment
    "Kilesa",                    # Defilements
    "Nivarana",                  # Hindrances
    "Dana",                      # Generosity
    "Bhavana",                   # Cultivation / meditation
    "Sati",                      # Mindfulness (Pali)

    # =========================================================================
    # ADDITIONAL LOCATIONS REFERENCED IN TNH TEACHINGS
    # =========================================================================

    "Vulture Peak",
    "Gridhrakuta",               # Sanskrit: Vulture Peak
    "Rajagriha",                 # Ancient city near Vulture Peak
    "Sarnath",                   # Site of first sermon
    "Bodh Gaya",                 # Place of enlightenment
    "Manasarowara",              # Sacred lake referenced in sutras
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
