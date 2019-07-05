import json
from datasette.utils.asgi import Response
from .base import BaseView


class JsonDataView(BaseView):
    name = "json_data"

    def __init__(self, datasette, filename, data_callback):
        self.ds = datasette
        self.filename = filename
        self.data_callback = data_callback

    async def get(self, request, as_format):
        data = self.data_callback()
        if as_format:
            headers = {}
            if self.ds.cors:
                headers["Access-Control-Allow-Origin"] = "*"
            return Response(
                json.dumps(data),
                content_type="application/json; charset=utf-8",
                headers=headers,
            )

        else:
            return await self.render(
                ["show_json.html"],
                request=request,
                context={"filename": self.filename, "data": data},
            )
