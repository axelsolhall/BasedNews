"""Countries on the site: name, ISO 3166-1 numeric id (the world-atlas map's ids) and region (index into REGIONS)."""
REGIONS = ["Nordics", "Western Europe", "Southern Europe", "Central Europe & Baltics", "Eastern Europe & Balkans",
           "North America"]
COUNTRIES = [  # name, ISO 3166-1 numeric (world-atlas ids), region index
    ("Norway", "578", 0), ("Sweden", "752", 0), ("Denmark", "208", 0), ("Finland", "246", 0), ("Iceland", "352", 0),
    ("United Kingdom", "826", 1), ("Ireland", "372", 1), ("France", "250", 1), ("Belgium", "056", 1),
    ("Netherlands", "528", 1), ("Luxembourg", "442", 1), ("Germany", "276", 1), ("Austria", "040", 1),
    ("Switzerland", "756", 1),
    ("Spain", "724", 2), ("Portugal", "620", 2), ("Italy", "380", 2), ("Malta", "470", 2), ("Greece", "300", 2),
    ("Cyprus", "196", 2),
    ("Poland", "616", 3), ("Czechia", "203", 3), ("Slovakia", "703", 3), ("Hungary", "348", 3), ("Slovenia", "705", 3),
    ("Croatia", "191", 3), ("Estonia", "233", 3), ("Latvia", "428", 3), ("Lithuania", "440", 3),
    ("Romania", "642", 4), ("Bulgaria", "100", 4), ("Ukraine", "804", 4), ("Moldova", "498", 4), ("Serbia", "688", 4),
    ("Bosnia and Herzegovina", "070", 4), ("Montenegro", "499", 4), ("North Macedonia", "807", 4), ("Albania", "008", 4),
    ("United States", "840", 5), ("Canada", "124", 5), ("Mexico", "484", 5),
]
