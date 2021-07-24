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

    provider = fields.Selection(selection_add=[('wenjoy', 'Wenjoy')])

    wenjoy_api_key = fields.Char(string="Wenjoy Api Key", required_if_provider='wenjoy', groups='base.group_user')
    wenjoy_private_api_key = fields.Char(string="Wenjoy Private Api Key", required_if_provider='wenjoy', groups='base.group_user')
    wenjoy_website_url = fields.Char(string="Website base URL (ex: http://example.com)", required_if_provider='wenjoy', groups='base.group_user')

    @api.model
    def _get_wenjoy_urls(self, environment):
        """ Wenjoy URLs"""
        if environment == 'prod':
            return 'https://wenjoy.com.co/api/1.0/pc/post-checkout'
        return 'https://staging.wenjoy.com.co/api/1.0/pc/post-checkout'

    @api.multi
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

    @api.multi
    def wenjoy_form_generate_values(self, values):
        # base_url = self.get_base_url()

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
            response_url=urls.url_join(self.wenjoy_website_url, '/payment/wenjoy/response'),
            confirmation_url=urls.url_join(self.wenjoy_website_url, '/payment/wenjoy/response'),
        )

        wenjoy_values['signature'] = self._wenjoy_generate_sign(wenjoy_values, False)

        return wenjoy_values

    @api.multi
    def wenjoy_get_form_action_url(self):
        self.ensure_one()
        return self._get_wenjoy_urls(self.environment)



class PaymentTransactionWenjoy(models.Model):
    _inherit = 'payment.transaction'

    @api.model
    def _wenjoy_form_get_tx_from_data(self, data):
        reference, sign, total_value, state = data.get('purchase_description'), data.get('purchase_signature'), data.get('purchase_total_value'), data.get('purchase_state')

        if not reference or not sign or not total_value or not state:
            raise ValidationError(_('Wenjoy: received data with missing reference (%s) or sign (%s)') % (reference, sign))

        transaction=self.env['payment.transaction'].search([('reference' ,'=' ,reference)])

        if not transaction:
            error_msg = (_('Wenjoy: received data for reference %s; no order found') % (reference))
            raise ValidationError(error_msg)
        elif len(transaction) > 1:
            error_msg = (_('Wenjoy: received data for reference %s; multiple orders found') % (reference))
            raise ValidationError(error_msg)

        # -------- Verify Signature HERE
        sign_check = transaction[0].acquirer_id._wenjoy_generate_sign(data, True)

        if sign_check != sign:
            raise ValidationError(('invalid sign, received %s, computed %s') % (sign, sign_check))
        else:
            # Resolve Wenjoy TX
            # Get Transacction And Order
            _transaction = transaction[0]
            order_id = self.get_order_id(_transaction.id)
            _order = self.env["sale.order"].browse(int(order_id))

            # WJ Logic
            status = state

            if status == 'PURCHASE_FINISHED':
                _transaction.sudo().update({"state": "done"})
                _order.sudo().update({'reference':_transaction.reference})
                _order.sudo().action_confirm()
                _order.action_quotation_send()
            elif status == 'PURCHASE_STARTED':
                _transaction.sudo().update({"state": "pending"})
                _order.sudo().action_quotation_send()
                _order.sudo().update({"state": "sent"})
            elif status == 'PURCHASE_REJECTED':
                _transaction.sudo().update({"state": "error"})
            else:
                pass

        return transaction[0]
    
    @api.multi
    def _wenjoy_form_get_invalid_parameters(self, data):
        invalid_parameters = []
        return invalid_parameters
        
    @api.multi
    def _wenjoy_form_validate(self, data):
        result = self.write({
            'acquirer_reference': data.get('purchase_description'),
            'state_message': data.get('purchase_state'),
            'date':fields.Datetime.now()
        })
        return result

    def get_order_id(self, transaction_id):
        query = "select * from sale_order_transaction_rel where transaction_id = '" + str(transaction_id) + "'"
        self.env.cr.execute(query)
        transactions_rel = self.env.cr.dictfetchone()
        if("sale_order_id" in transactions_rel):
            return transactions_rel["sale_order_id"]
        return None