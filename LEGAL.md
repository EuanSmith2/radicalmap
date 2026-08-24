# Legal Notice and Research Disclaimer

## RadicalMap — Research Instrument

**Developed by:** Euan Smith  
**Project type:** Independent academic research / methodological proof-of-concept  
**Date:** 2026

---

## 1. Research Disclaimer

This tool was developed strictly for academic research, counter-disinformation analysis, and educational purposes. It catalogues, classifies, and visualises extremist content that is **publicly accessible** on short-form video platforms.

**The researcher, Euan Smith, does not hold, endorse, promote, or affiliate with any of the ideologies, movements, organisations, or viewpoints documented within this tool.** The content analysed — including white nationalist material, antisemitic content, and radicalisation-adjacent media — is reproduced here solely for the purposes of identification, classification, and academic study.

This project represents a **small-scale methodological sample**. The 18-video dataset used in development was selected to demonstrate the tool's detection and classification capabilities across a single interconnected content cluster. It is not a comprehensive or statistically representative survey.

---

## 2. Statutory Basis for Research Use

### 2.1 GDPR — Scientific Research Exemption

The collection and processing of publicly accessible social media metadata in this project is conducted under the **scientific research exemption** provided by:

**Regulation (EU) 2016/679 (General Data Protection Regulation), Article 89(1):**

> "Processing for archiving purposes in the public interest, scientific or historical research purposes or statistical purposes, shall be subject to appropriate safeguards, in accordance with this Regulation, for the rights and freedoms of the data subject. Those safeguards shall ensure that technical and organisational measures are in place in particular in order to ensure respect for the principle of data minimisation."

**GDPR Recital 159** further clarifies:

> "When personal data are processed for scientific research purposes, this Regulation should also apply to that processing. For the purposes of this Regulation, the processing of personal data for scientific research purposes should be interpreted in a broad manner including for example technological development and demonstration, fundamental research, applied research and privately funded research."

**Irish Data Protection Act 2018, Section 42(1):**

> "Personal data may be processed for archiving purposes in the public interest, scientific or historical research purposes or statistical purposes subject to appropriate safeguards for the rights and freedoms of data subjects..."

No data held by this project links individuals to sensitive characteristics beyond what they have publicly self-disclosed. Data minimisation has been applied: only metadata (captions, usernames, hashtags, engagement statistics, bios) is stored. Video files, where downloaded for local analysis, are not distributed.

### 2.2 Text and Data Mining — Copyright Exception

The automated collection of publicly accessible metadata (captions, hashtags, usernames, engagement counts) is conducted under the **text and data mining exception** for scientific research:

**Directive (EU) 2019/790 on Copyright in the Digital Single Market (CDSM Directive), Article 3(1):**

> "Member States shall provide for an exception to the rights provided for in Article 5(a) and Article 7(1) of Directive 96/9/EC, Article 2 of Directive 2001/29/EC, and Article 15(1) of this Directive for reproductions and extractions made by research organisations and cultural heritage institutions in order to carry out, for the purposes of scientific research, text and data mining of works or other subject matter to which they have lawful access."

This exception was transposed into Irish law by **Statutory Instrument No. 567 of 2021** (European Union (Copyright and Related Rights in the Digital Single Market) Regulations 2021), amending the **Copyright and Related Rights Act 2000**.

### 2.3 Digital Services Act — Researcher Access Framework

This tool contributes to the research use case established by:

**Regulation (EU) 2022/2065 (Digital Services Act), Article 40(4):**

> "Providers of very large online platforms or of very large online search engines shall provide, at the request of the Digital Services Coordinator of establishment, or the Commission, access to data to vetted researchers who meet the requirements set out in paragraph 8 for the sole purpose of conducting research that contributes to the detection, identification and understanding of systemic risks in the Union as set out in Article 34(1) and to the assessment of the adequacy, efficiency and impacts of the risk mitigation measures pursuant to Article 35."

While this project operates independently of the formal DSA vetting mechanism, its methodology directly supports the research goals enumerated in Article 40 — specifically the detection and classification of content that constitutes systemic risk under **Article 34(1)(c)**:

> "...actual or foreseeable negative effects on civic discourse and electoral processes, and public security."

### 2.4 Relevance to Irish Criminal Law

The content catalogued by this tool may constitute offences under Irish law. Documentation of such content is a prerequisite for referral to law enforcement. The relevant statutory framework includes:

**Criminal Justice (Incitement to Violence or Hatred and Hate Offences) Act 2024 (No. 34 of 2024), Section 7:**

> "A person who prepares or possesses material — (a) with a view to the material being communicated to the public or a section of the public, whether by himself or herself or another person, and (b) with intent to incite violence against, or hatred of, a person or a group of persons on account of their protected characteristics or any of those characteristics, is guilty of an offence."

**Section 10** of the same Act addresses the distribution of material likely to incite hatred, with penalties of up to five years' imprisonment on indictment.

The identification of content falling within the scope of this legislation is a legitimate and necessary research activity in support of enforcement, policy development, and platform accountability.

### 2.5 Freedom of Expression and Academic Freedom

Research into harmful content is protected as a legitimate exercise of freedom of expression for academic and public interest purposes:

**European Convention on Human Rights, Article 10(1):**

> "Everyone has the right to freedom of expression. This right shall include freedom to hold opinions and to receive and impart information and ideas without interference by public authority and regardless of frontiers."

**Article 10(2)** recognises that restrictions on expression must be "necessary in a democratic society" — but critically, *cataloguing* extremist content for research purposes does not constitute the promotion or publication of such content, and therefore falls outside the scope of permissible restrictions.

**Bunreacht na hÉireann (Irish Constitution), Article 40.6.1°(i):**

> "The State guarantees liberty for the exercise of the following rights, subject to public order and morality: The right of the citizens to express freely their convictions and opinions."

Academic research into the mechanisms of radicalisation is a constitutionally protected activity.

---

## 3. Data Handling

- **What is collected:** Publicly available metadata — video captions, usernames, account bios, hashtags, engagement statistics (views, likes, comments, shares, saves). No private or non-public data is accessed.
- **What is not collected:** Private messages, non-public account data, payment information, or any data requiring authenticated access beyond standard public viewing.
- **Storage:** Data is held locally in a SQLite database. The database file is not distributed as part of this repository.
- **Video files:** Where videos are downloaded for local transcript analysis, they are stored locally only and are not distributed. They remain the intellectual property of their respective creators and platform.
- **Retention:** No data retention policy is mandated for local research installations. Users deploying this tool are responsible for compliance with applicable data protection law in their jurisdiction.

---

## 4. Intended Use

This tool is designed for use by:

- Academic researchers in criminology, sociology, political science, and psychology
- Platform trust and safety teams (social media companies)
- Policy researchers and legislative advisors
- Law enforcement bodies and intelligence analysts
- Counter-disinformation organisations operating in the public interest

**This tool is not designed for, and must not be used for:**

- Surveillance of individuals outside a public-interest research context
- Targeted harassment or identification of private individuals
- Any commercial purpose involving the resale of scraped data
- Any purpose that would itself constitute incitement to hatred or violence

---

## 5. Platform Terms of Service

TikTok's Terms of Service (Section 2.1, effective 2024) restrict automated access to the platform. The metadata collection methodology in this project uses `yt-dlp`, an open-source tool, to access publicly available content. The researcher notes that:

1. All content analysed was publicly accessible without authentication at the time of collection.
2. The collection is non-commercial and conducted for research purposes.
3. The DSA (Article 40) creates a legal obligation on Very Large Online Platforms (VLOPs) to facilitate researcher access to data for systemic risk research — a right that supersedes platform-level contractual restrictions under EU law to the extent necessary for legitimate research.

---

## 6. Contact

For research enquiries, collaboration, or legal questions:

**Euan Smith** — [euansmith.net](https://euansmith.net)

---

*This legal notice was prepared in good faith with reference to applicable EU and Irish law as at 2026. It does not constitute legal advice. Users of this tool are responsible for assessing the legal framework applicable in their own jurisdiction.*
