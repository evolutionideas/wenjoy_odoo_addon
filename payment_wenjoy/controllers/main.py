# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import pprint
import werkzeug

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WenjoyController(http.Controller):

    @http.route('/payment/wenjoy/response', type='http', auth='public', csrf=False)
    def wenjoy_response(self, **post):
        _logger.info('Response data %s', pprint.pformat(post))
        if post:
            request.env['payment.transaction'].sudo().form_feedback(post, 'wenjoy')
        return werkzeug.utils.redirect('/payment/process')