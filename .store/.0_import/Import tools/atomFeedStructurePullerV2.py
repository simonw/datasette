import requests
from bs4 import BeautifulSoup

url = "https://www.upwork.com/ab/feed/jobs/atom?q=Photoshop&sort=recency&job_type=hourly%2Cfixed&proposals=0-4%2C5-9%2C10-14%2C15-19&budget=50-&verified_payment_only=1&hourly_rate=25-&paging=0%3B50&api_params=1&securityToken=5d53fdd5809c340cfe7034341784f715c8433ec350191c7f0ee5607d91b69227dc8481c3ef14b654d0a87a7211a52622e824e33ae3cce04de57c7398856752ec&userUid=1327243219150897152&orgUid=1327243219155091457"
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