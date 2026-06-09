def extra_names_from_request(request):
    extra_bits = request.args.getlist("_extra")
    extras = set()
    for bit in extra_bits:
        extras.update(part for part in bit.split(",") if part)
    return extras
