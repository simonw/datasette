from datasette import hookimpl


@hookimpl
def extra_template_vars(view_name, request):
    return {
        "view_name": view_name,
        "request": request,
    }
