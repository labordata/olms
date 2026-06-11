"""Base for the @*_form contract tags used in spider docstrings.

The OLMS servlets are form-encoded POSTs; subclass FormContract with a
`name` and the `formdata` that makes the tagged endpoint respond, e.g.:

    class FilersFormContract(FormContract):
        name = "filers_form"
        formdata = {"clearCache": "F", "page": "1"}
"""

from urllib.parse import urlencode

from scrapy.contracts import Contract


class FormContract(Contract):
    formdata = {}

    def adjust_request_args(self, args):
        args["method"] = "POST"
        args["body"] = urlencode(self.formdata)
        headers = args.setdefault("headers", {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return args
