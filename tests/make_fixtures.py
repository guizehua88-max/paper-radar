"""Build offline fixtures from the real Crossref results fetched on 2026-10-28 (titles/journals/dates/DOIs are real;
author and abstract fields are left empty because those queries did not return them)."""
import json, pathlib
REAL = [
 # (title, journal, created, type, doi)
 ("Ultrafast, catalyst-free synthesis of highly ordered graphitic carbon from hierarchically ordered cellulose nanocrystals by flash Joule heating","Journal of Analytical and Applied Pyrolysis","2026-10-02","journal-article","10.1016/j.jaap.2026.108165"),
 ("Ultrafast Joule heating synthesis of carbon nanomaterials for electrochemical energy storage and conversion","EnergyChem","2026-10-13","journal-article","10.1016/j.enchem.2026.100211"),
 ("Joule heating shock synthesis of (Fe,Ni)@FLG catalyst for improved hydrogen storage kinetics and cycling stability of MgH2","Journal of Alloys and Compounds","2026-08-15","journal-article","10.1016/j.jallcom.2026.190422"),
 ("CuNiAgCe medium-entropy metallic glass synthesized via pulsed Joule heating for electrocatalytic nitrate-to-ammonia conversion","Chemical Communications","2026-10-02","journal-article","10.1039/d6cc04892g"),
 ("Emerging strategies for catalytic ammonia synthesis: novel energy-input pathways and regulation of catalyst electronic structure","Chemical Communications","2026-09-25","journal-article","10.1039/d6cc04989c"),
 ("Rapid Joule heating enables MOF-derived alloy nanoparticle interlocked within graphitic carbon matrix for efficient and durable nitrate-to-ammonia electrocatalysis","Chemical Engineering Journal","2026-10-10","journal-article","10.1016/j.cej.2026.182947"),
 ("Precision Synthesis of Biomass-Derived Graphene-Like Carbon With Tailored Crystallinity via Controlled Flash Joule Heating","Carbon Energy","2026-08-21","journal-article","10.1002/cey2.70308"),
 ("Nonstoichiometric Uranium Dinitride as a Potential Catalyst for Ammonia Synthesis: A Theoretical Study of Surface Science","ChemPhysChem","2026-10-28","journal-article","10.1002/cphc.70594"),
 ("Ultrafast Joule heating synthesis of Ru–Mo2C catalysts for efficient alkaline hydrogen evolution","New Journal of Chemistry","2026-08-26","journal-article","10.1039/d6nj02497a"),
 ("Rapid flash Joule heating synthesis of Pt-doped carbon fibers for efficient hydrogen evolution in acidic and alkaline media","Diamond and Related Materials","2026-08-17","journal-article","10.1016/j.diamond.2026.114086"),
 ("Elucidating the Role of Nickel Catalyst Properties, Synthesis Methods, Process Variables, and Reactor Geometry on Hydrogen Production from Ammonia","ACS Sustainable Chemistry & Engineering","2026-09-03","journal-article","10.1021/acssuschemeng.6c05957"),
 ("Thirty-second Joule-heating synthesis of Cu-intercalated Mo6S8 Chevrel phases with pH-symmetric hydrogen evolution behavior","RSC Advances","2026-10-19","journal-article","10.1039/d6ra05873f"),
 ("Ceria support enhances activity of CoNi catalysts for ammonia synthesis under magnetic heating","RSC Advances","2026-09-02","journal-article","10.1039/d6ra05773j"),
 ("Flash Joule heating for rapid MXenes synthesis","Nature Synthesis","2026-08-10","journal-article","10.1038/s44160-026-01132-2"),
 ("High-resolution spatiotemporal mapping of transient Joule heating and skin tissue responses during microneedle electroporation","Bioelectrochemistry","2026-08-06","journal-article","10.1016/j.bioelechem.2026.109421"),
 ("The influence of Joule heating on electrical properties of intrinsic Josephson junctions","Low Temperature Physics","2026-10-01","journal-article","10.1063/10.0046491"),
 ("Non-equilibrium Joule heating for electrocatalysts: a coordination chemistry perspective","Coordination Chemistry Reviews","2026-09-02","journal-article","10.1016/j.ccr.2026.218467"),
 ("Joule-heating synthesis of Sn-In bimetallic freestanding electrode for efficient electrocatalytic CO2 reduction to formic acid","Journal of Alloys and Compounds","2026-08-29","journal-article","10.1016/j.jallcom.2026.190779"),
 ("Rapid low-energy formation of conductive beaded structures via Joule heating","Materials & Design","2026-10-23","journal-article","10.1016/j.matdes.2026.117309"),
 ("Enhanced ammonia synthesis activity from inherent anion vacancies in a pyrochlore oxynitride catalyst support","Journal of Catalysis","2026-10-02","journal-article","10.1016/j.jcat.2026.117220"),
 ("Electroosmotic–Peristaltic Propulsion of Ionic Jeffrey Fluid in Hall-Current-Modulated Magnetised Ciliated Porous Microchannel With Joule Heating and Soret–Dufour Cross-Diffusion","ELECTROPHORESIS","2026-09-10","journal-article","10.1002/elps.70152"),
 ("Three dimensional maxwell tetra hybrid nanofluids flow over a Riga plate: Effect of couple stress, thermal radiation, and joule heating","Thermal Advances","2026-08-06","journal-article","10.1016/j.thradv.2026.100201"),
 ("Comparative MHD transport analysis of nano, hybrid and ternary hybrid nanofluids with thermophoresis, Brownian motion, Joule heating and viscous dissipation","Chemical Product and Process Modeling","2026-09-24","journal-article","10.1515/cppm-2026-0055"),
 ("Inhibition of graphitization on diamond surfaces via flash joule heating and construction of chemical bonds for tungsten metallization","Applied Physics Letters","2026-08-07","journal-article","10.1063/5.0343526"),
 ("Heat Transfer Optimization of Ternary Nanofluid Flow over a Thin Needle with Joule Heating: Artificial Neural Network Approach","International Journal For Multidisciplinary Research","2026-09-02","journal-article","10.36948/ijfmr.2026.v08i04.86750"),
 ("Joule-lenz Self-heating Synthesis of SnO2/Au Nanosheets for High-sensitivity Surface-enhanced Raman Scattering Applications","VNU Journal of Science: Mathematics - Physics","2026-10-11","journal-article","10.25073/2588-1124/vnumap.5162"),
 ("Seconds-scale synthesis of heteroatom-doped nickel oxides via non-equilibrium flash Joule heating for coupled biomass valorization and zinc–air batteries","Chemical Communications","2026-09-04","journal-article","10.1039/d6cc04155h"),
 ("Stretchable Multifunctional TPU/Field's Metal Films with Integrated Electromagnetic Shielding, Joule Heating, and Shape Memory","ACS Applied Materials & Interfaces","2026-09-24","journal-article","10.1021/acsami.6c14763"),
 ("Study of Joule heating over Gannon storm using multiscale atmosphere geo-space environment model","Journal of Space Safety Engineering","2026-09-11","journal-article","10.1016/j.jsse.2026.08.009"),
 ("First-Principles Microkinetic Model for Ammonia Synthesis on Fe(110) under Nonequilibrium Programmable Heating and Quenching Operation","ACS Catalysis","2026-09-05","journal-article","10.1021/acscatal.6c03929"),
 # from the flash-Joule query
 ("Flash Joule heating-driven reduced graphene oxide encapsulation of Mg-based alloys for high-performance and stable hydrogen storage","Journal of Materials Science & Technology","2026-10-20","journal-article","10.1016/j.jmst.2026.10.003"),
 ("Ultrafast Joule-heating fabrication of high-strength, high-resistivity and low-k SiC fibers via suppressing carbon ordering","Journal of the European Ceramic Society","2026-10-12","journal-article","10.1016/j.jeurceramsoc.2026.118936"),
 ("Effect of ammonia injection pressure and injector nozzle holes on flash-boiling free sprays","Fuel","2026-10-09","journal-article","10.1016/j.fuel.2026.141219"),
 ("Predictive modeling of flash-boiling ammonia sprays using a volume expansion ratio","Fuel","2026-10-15","journal-article","10.1016/j.fuel.2026.141500"),
 ("Sustainable ohmic heating drives multiscale structural reorganization in whole faba bean flour, reconfiguring water dynamics and IDDSI-oriented gel functionality","Food Hydrocolloids","2026-10-05","journal-article","10.1016/j.foodhyd.2026.113422"),
 ("Engineering ruthenium catalyst inside hollow mesoporous silica nanoreactors via flash nanoprecipitation for selective hydrogenation of furfural","Journal of Colloid and Interface Science","2026-10-07","journal-article","10.1016/j.jcis.2026.141828"),
 # from the ALD query (supporting-info components and non-catalysis ALD = noise)
 ("Interfacial Modification of Pd/ZrO2 Catalyst by Atomic-Layer In2O3 for Enhanced CO2 Hydrogenation to Methanol","","2026-10-07","component","10.1021/acs.iecr.6c02734.s001"),
 ("Atomic Layer Deposition of Ta2O5 on Composite Nickel-rich Cathodes for High Voltage Lithium-ion Battery","","2026-08-13","component","10.1021/acsaem.6c01451.s001"),
 ("ALD pour le photovoltaïque","Photovoltaïque","2026-09-07","journal-article","10.51257/a-v2-re257"),
 ("Atomic Layer Deposition (ALD)","Encyclopedia of Nanotechnology","2026-10-15","book-chapter","10.1007/978-94-017-9780-1_100053"),
]
items = []
for t, j, d, typ, doi in REAL:
    y, m, dd = map(int, d.split("-"))
    items.append({"DOI": doi, "title": [t], "container-title": [j] if j else [], "type": typ,
                  "created": {"date-parts": [[y, m, dd]]}, "issued": {"date-parts": [[y, m, dd]]}})
out = pathlib.Path(__file__).parent / "fixtures"
(out / "crossref.json").write_text(json.dumps({"status": "ok", "message": {"items": items}}, ensure_ascii=False), "utf-8")
(out / "arxiv.xml").write_text('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>arXiv</title></feed>', "utf-8")
(out / "rss_missing.xml").write_text('<?xml version="1.0"?><rss version="2.0"><channel><title>empty</title></channel></rss>', "utf-8")
print(len(items), "crossref fixture items")
