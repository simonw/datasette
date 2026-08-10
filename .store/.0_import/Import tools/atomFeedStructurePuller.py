import requests
from bs4 import BeautifulSoup

url = "https://example.com/atom_feed.xml"
response = requests.get(url)
soup = BeautifulSoup(response.text, 'xml')

feed_elements = {}

for element in soup.find_all():
    tag_name = element.name
    if tag_name not in feed_elements:
        feed_elements[tag_name] = []

    attributes = {}
    for attr, value in element.attrs.items():
        attributes[attr] = value

    feed_elements[tag_name].append(attributes)

for tag_name, attributes_list in feed_elements.items():
    print(f"{tag_name}:")
    for attributes in attributes_list:
        print(f"  - {attributes}")