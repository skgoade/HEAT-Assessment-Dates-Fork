"""Unit tests for M1 hitting-assessment validation (no DB required)."""
import unittest

from main import validate_assessment_data


class ValidateAssessmentDataTests(unittest.TestCase):
    def test_initial_ok(self):
        data = {
            "playerName": "Jason Peele",
            "assessmentDate": "2026-07-01",
            "assessmentType": "initial",
        }
        ok, err = validate_assessment_data(data)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertIsNone(data["_previousAssessmentId"])

    def test_retest_with_previous_only(self):
        data = {
            "playerName": "Jason Peele",
            "assessmentDate": "2026-07-20",
            "assessmentType": "retest",
            "previousAssessmentId": 15,
        }
        ok, err = validate_assessment_data(data)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(data["_previousAssessmentId"], 15)

    def test_retest_omitted_previous_ok(self):
        data = {
            "playerName": "Jason Peele",
            "assessmentDate": "2026-07-20",
            "assessmentType": "retest",
        }
        ok, err = validate_assessment_data(data)
        self.assertTrue(ok)
        self.assertIsNone(data["_previousAssessmentId"])

    def test_initial_clears_previous(self):
        data = {
            "playerName": "Jason Peele",
            "assessmentDate": "2026-07-01",
            "assessmentType": "initial",
            "previousAssessmentId": 99,
        }
        ok, err = validate_assessment_data(data)
        self.assertTrue(ok)
        self.assertIsNone(data["_previousAssessmentId"])


if __name__ == "__main__":
    unittest.main()
