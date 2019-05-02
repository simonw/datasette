class MockRequest:
    def __init__(self, url):
        self.url = url
        self.path = "/" + url.split("://")[1].split("/", 1)[1]
        self.query_string = ""
        if "?" in url:
            self.query_string = url.split("?", 1)[1]
            self.path = self.path.split("?")[0]
