"""Focused false-positive regressions and nearby true positives; no network."""
import pathlib
import sys
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar.filters import Classifier


class FilterRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clf = Classifier(tomllib.loads((ROOT / "config.toml").read_text("utf-8")))

    def classify(self, title, abstract=""):
        return self.clf.classify({"title": title, "abstract": abstract})

    def test_real_fluid_dissipation_title_is_not_material_synthesis(self):
        # DOI 10.1080/02286203.2026.2748852; "nanoparticles" used to satisfy require_any.
        self.assertIsNone(self.classify(
            "Modeling and numerical simulations on the effects of nanoparticles, viscous and Joule heating "
            "dissipation on thermal energy and mass transfer enhancement in Herschel-Bulkley fluid in "
            "Darcy-Forchheimer porous medium"))

    def test_cost_paper_stays_background_even_with_secondary_electrochemistry(self):
        # Real title, DOI 10.26434/chemrxiv.15011227/v1; a secondary-route mention must not bypass the cost rule.
        p = self.classify("Can existing or future pathways reduce green ammonia production costs?",
                          "We compare electrochemical ammonia synthesis with the Haber-Bosch process using N2 and H2.")
        self.assertEqual(p["tier"], "background")

    def test_factory_and_framework_titles_stay_in_library_without_push_priority(self):
        for title in ("Biomass gasification for green ammonia production in kraft mills",
                      "A Multi-Scale Modelling Framework for Ammonia Production"):
            with self.subTest(title=title):
                p = self.classify(title)
                self.assertEqual(p["tier"], "background")
                self.assertLess(p["score"], 18)

    def test_deposition_reaction_catalyst_is_not_catalytic_application(self):
        # Existing records: 10.1016/j.apsusc.2026.168824 and 10.1116/6.0005731.
        for title in (
            "Small-molecule inhibitors for suppressing pyridine-catalyzed SiO2 atomic layer deposition on Al2O3: "
            "screening and mechanistic Insights into acetylacetone passivation",
            "Pyridine-catalyzed molecular layer deposition of silicon oxycarbide and structural evolution upon thermal annealing",
            "Effect of in-cycle nitrogen plasma treatment on plasma-enhanced atomic layer deposited silicon nitride thin films "
            "using diiodosilane and ammonia plasma",
        ):
            with self.subTest(title=title):
                p = self.classify(title)
                self.assertEqual(p["tier"], "background")
                self.assertLess(p["score"], 18)
        self.assertEqual(self.classify(
            "Pyridine-catalyzed atomic layer deposition of SiO2",
            "Pyridine acts as a catalyst for the deposition reaction. Thin-film growth and passivation are investigated.",
        )["tier"], "background")

    def test_real_catalytic_use_overrides_uncertain_ald_process_label(self):
        p = self.classify("Pyridine-catalyzed atomic layer deposition of Ru for ammonia synthesis",
                          "The resulting catalyst accelerates N2 dissociation in ammonia synthesis from N2 and H2.")
        self.assertEqual(p["tier"], "cross")
        p = self.classify("Pyridine-catalyzed molecular layer deposition of silicon oxycarbide",
                          "The resulting material is used for selective hydrogenation catalysis.")
        self.assertEqual(p["tier"], "transferable")

    def test_reactor_models_and_cost_reduction_catalysts_are_preserved(self):
        for title, tier in (
            ("Numerical investigation of a Joule-heated catalytic reactor for enhanced ammonia decomposition", "transferable"),
            ("Low-cost Ru catalysts for efficient ammonia synthesis", "direct"),
            ("Multi-scale modelling of ammonia synthesis kinetics on Fe catalysts", "direct"),
            ("Atomic layer deposition of FeOx overcoats on Pt nanoparticles for selective hydrogenation", "transferable"),
            ("Carbothermal shock synthesis of high-entropy alloy nanoparticles", "transferable"),
        ):
            with self.subTest(title=title):
                self.assertEqual(self.classify(title)["tier"], tier)


if __name__ == "__main__":
    unittest.main()
