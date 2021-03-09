# coding: utf-8
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import uuid
import hashlib

from werkzeug import urls
from datetime import datetime

from odoo import api, fields, models, _
from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.tools.float_utils import float_compare


_logger = logging.getLogger(__name__)


class PaymentAcquirerWenjoy(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[
        ('wenjoy', 'Wenjoy')
    ], ondelete={'wenjoy': 'set default'})

    wenjoy_api_key = fields.Char(string="Wenjoy Api Key", required_if_provider='wenjoy', groups='base.group_user')
    wenjoy_private_api_key = fields.Char(string="Wenjoy Private Api Key", required_if_provider='wenjoy', groups='base.group_user')

    def _get_wenjoy_urls(self, environment):
        """ Wenjoy URLs"""
        if environment == 'prod':
            return 'https://wenjoy.com.co/api/1.0/pc/post-checkout'
        return 'https://staging.wenjoy.com.co/api/1.0/pc/post-checkout'

    
    def _wenjoy_generate_sign(self, values, private):

        if private:
            string_values_signature = str(self.wenjoy_private_api_key) + "~" + \
                            str(values.get('purchase_total_value')) + "~" + \
                            str(values.get('purchase_description')) + "~" + \
                            str(values.get('purchase_state'))
        else:
            string_values_signature = str(values['api_key']) + "~" + \
                                str(values['total_value']) + "~" + \
                                str(values['description']) + "~" + \
                                str(values['verify']).lower()
            
            string_values_signature += '~0~0~0'

        return hashlib.sha256(str.encode(string_values_signature)).hexdigest()


    def wenjoy_form_generate_values(self, values):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        tx = self.env['payment.transaction'].search([('reference', '=', values.get('reference'))])

        if tx.state not in ['done', 'pending']:
            tx.reference = str(uuid.uuid4())

        wenjoy_values = dict(
            values,
            total_value=int(round(values['amount'])),
            description=tx.reference,
            api_key=self.wenjoy_api_key,
            verify='false',
            owner_email=values['partner_email'],
            owner_first_name=values['partner_first_name'],
            owner_last_name=values['partner_last_name'],
            response_url=urls.url_join(base_url, '/payment/wenjoy/response'),
            confirmation_url=urls.url_join(base_url, '/payment/wenjoy/response'),
        )

        wenjoy_values['signature'] = self._wenjoy_generate_sign(wenjoy_values, False)

        return wenjoy_values


    def wenjoy_get_form_action_url(self):
        self.ensure_one()
        environment = 'prod' if self.state == 'enabled' else 'test'
        return self._get_wenjoy_urls(environment)



class PaymentTransactionWenjoy(models.Model):
    _inherit = 'payment.transaction'

    @api.model
    def _wenjoy_form_get_tx_from_data(self, data):
        reference, sign, total_value, state = data.get('purchase_description'), data.get('purchase_signature'), data.get('purchase_total_value'), data.get('purchase_state')

        if not reference or not sign or not total_value or not state:
            raise ValidationError(_('Wenjoy: received data with missing reference (%s) or sign (%s)') % (reference, sign))

        transaction = self.search([('reference', '=', reference)])

        if not transaction:
            error_msg = (_('Wenjoy: received data for reference %s; no order found') % (reference))
            raise ValidationError(error_msg)
        elif len(transaction) > 1:
            error_msg = (_('Wenjoy: received data for reference %s; multiple orders found') % (reference))
            raise ValidationError(error_msg)

        # -------- Verify Signature HERE
        sign_check = transaction.acquirer_id._wenjoy_generate_sign(data, True)

        if sign_check != sign:
            raise ValidationError(('invalid sign, received %s, computed %s') % (sign, sign_check))

        return transaction
    

    def _wenjoy_form_get_invalid_parameters(self, data):
        invalid_parameters = []
        return invalid_parameters
        

    def _wenjoy_form_validate(self, data):
        self.ensure_one()

        status = data.get('purchase_state') or ""
        res = {
            'acquirer_reference': data.get('purchase_description') or "",
            'state_message': data.get('purchase_state') or ""
        }

        if status == 'PURCHASE_FINISHED':
            res.update(state='done', date=fields.Datetime.now())
            self._set_transaction_done()
            self.write(res)
            self.execute_callback()
            return True
        elif status == 'PURCHASE_STARTED':
            res.update(state='pending')
            self._set_transaction_pending()
            return self.write(res)
        elif status == 'PURCHASE_REJECTED':
            res.update(state='cancel')
            self._set_transaction_cancel()
            return self.write(res)
        else:
            error = 'Invalid State: %s' % (status)
            res.update(state='cancel', state_message=error)
            self._set_transaction_cancel()
            return self.write(res)