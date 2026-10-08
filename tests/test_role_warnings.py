#!/usr/bin/env python3
"""#55: Beratende Warnungen bei riskanten Rollen-Kombinationen (role_risk_warnings); blockieren nie das Speichern.

Aufruf:  python tests/test_role_warnings.py
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_roles_rights as base  # noqa: E402  (richtet Temp-DB und Server-Helfer ein)

server = base.server
codes = lambda p: [w['code'] for w in server.role_risk_warnings(p)]


class Rules(unittest.TestCase):
    def test_user_admin_with_operative_rights(self):
        p = {'baseRole': 'department_lead', 'rights': {}, 'actions': {'faCreate': False, 'prodFinish': False}}
        self.assertIn('MP-ROLE-011', codes(p))
        p['actions'].update(faPlan=False, userAdmin=False)
        self.assertNotIn('MP-ROLE-011', codes(p))

    def test_user_admin_irrelevant_for_non_manager_bases(self):
        self.assertEqual(codes({'baseRole': 'production', 'rights': {}, 'actions': {}}), [])
        self.assertEqual(codes({'baseRole': 'viewer', 'rights': {}, 'actions': {}}), [])

    def test_create_and_finish(self):
        p = {'baseRole': 'department_deputy', 'rights': {}, 'actions': {'userAdmin': False}}
        self.assertEqual(codes(p), ['MP-ROLE-012'])
        p['rights'] = {'planning': 'read'}
        self.assertEqual(codes(p), [])
        self.assertEqual(codes({'baseRole': 'department_deputy', 'rights': {}, 'actions': {'userAdmin': False, 'prodFinish': False}}), [])

    def test_user_admin_with_system_edit(self):
        p = {'baseRole': 'department_lead', 'rights': {'planning': 'read', 'system': 'edit'}, 'actions': {}}
        self.assertEqual(codes(p), ['MP-ROLE-013'])
        p['rights']['system'] = 'read'
        self.assertEqual(codes(p), [])

    def test_legacy_and_garbage_input(self):
        self.assertEqual(server.role_risk_warnings(None), [])
        self.assertEqual(server.role_risk_warnings({'baseRole': 'department_lead', 'actions': 'x', 'rights': 5})[0]['code'], 'MP-ROLE-011')
        for w in server.role_risk_warnings({'baseRole': 'department_lead'}):
            self.assertTrue(w['code'].startswith('MP-ROLE-') and w['text'])


class Api(base.RoleProfiles):
    def test_save_and_list_return_warnings_without_blocking(self):
        st, body, _ = self.put_role('risiko-lead', {'name': 'Risiko Lead', 'baseRole': 'department_lead'})
        self.assertEqual(st, 200, body)
        self.assertEqual({w['code'] for w in body['warnings']}, {'MP-ROLE-011', 'MP-ROLE-012', 'MP-ROLE-013'})
        st, body, _ = self.req('GET', '/api/roles', None, self.admin)
        prof = {p['id']: p for p in body['profiles']}['risiko-lead']
        self.assertEqual(len(prof['warnings']), 3)
        st, body, _ = self.put_role('sicher-lead', {'name': 'Sicher Lead', 'baseRole': 'department_lead', 'rights': {'system': 'read'},
                                                    'actions': {'userAdmin': False, 'faCreate': False}})
        self.assertEqual((st, body['warnings']), (200, []))

    test_profile_validation_and_management = None
    test_av_profile_is_enforced_on_server = None
    test_actions_and_endpoints = None


if __name__ == '__main__':
    unittest.main(verbosity=2, argv=[sys.argv[0]] + [a for a in sys.argv[1:]])
