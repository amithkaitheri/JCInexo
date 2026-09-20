# Requirements Document

## Introduction

The Smart Member Growth Tracker is a centralized dashboard that tracks a member's journey from Prospective Member (PM) through to induction and active membership. It replaces scattered, manually-maintained spreadsheets with a single system of record that automates alerts for age-out deadlines, tracks attendance milestones, computes a "member health" score to flag members at risk of dropping out, and preserves member data across the annual leadership handover ("One Year to Lead").

The primary problem this feature solves is data loss and manual tracking overhead during the annual leadership transition, when responsibility for member records passes from one leadership team to the next.

## Glossary

- **Member_Tracker**: The centralized system that stores member records, computes derived metrics, and presents the dashboard.
- **Member**: An individual record tracked by the Member_Tracker, in any stage of the membership journey.
- **Prospective_Member (PM)**: A Member who has expressed interest but has not yet been inducted.
- **Inducted_Member**: A Member who has completed the induction process and holds active membership.
- **Membership_Stage**: The current phase of a Member's journey. Allowed values: Prospective, Candidate, Inducted, Inactive.
- **Age_Out_Date**: The calendar date after which a Member is no longer eligible to remain in a given Membership_Stage (for example, an eligibility deadline for prospective status or an age-based eligibility cutoff).
- **Attendance_Milestone**: A defined threshold of attended events (for example, "attended 3 chapter meetings") that marks progress toward induction.
- **Attendance_Record**: A dated entry recording whether a Member attended a specific event.
- **Health_Score**: A numeric value from 0 to 100 computed by the Member_Tracker that indicates a Member's risk of dropping out, where higher values indicate lower risk.
- **At_Risk_Member**: A Member whose Health_Score is at or below the configured at-risk threshold.
- **Leadership_Handover**: The annual transition ("One Year to Lead") in which member-management responsibility passes from the outgoing leadership team to the incoming leadership team.
- **Chapter_Administrator**: A user responsible for managing members within a chapter and configuring tracking rules.
- **Handover_Snapshot**: A preserved, timestamped export of all member data captured at the time of a Leadership_Handover.
- **Natural_Language_Query**: A plain-English question submitted by a Chapter_Administrator that describes a set of Member records to retrieve, expressed in prose rather than as fixed dashboard filter selections.
- **Query_Interpreter**: The component of the Member_Tracker that translates a Natural_Language_Query into structured selection criteria over stored Member data and produces the matching set of Member records.
- **Interpreted_Criteria**: The structured, human-readable representation of the selection conditions that the Query_Interpreter derived from a Natural_Language_Query.
- **Query_Result_Set**: The set of Member records returned in response to a Natural_Language_Query, where every returned record is an existing Member record drawn from stored data.
- **AI_Agent (Wellington the Wise)**: The LLM-driven component of the Member_Tracker, presented under the persona "Wellington the Wise", that analyzes an At_Risk_Member, performs a web search for relevant local events, and synthesizes a Retention_Recommendation.
- **AI_Retention_Intervention**: A request to the AI_Agent to analyze a specific At_Risk_Member and produce a Retention_Recommendation aimed at re-engaging that Member.
- **Web_Search_Result**: A single result returned by the external web search, describing an upcoming local seminar, networking event, or skill workshop, including at minimum an event title, an event date, and a source URL.
- **Retention_Recommendation**: The structured output produced by the AI_Agent for an At_Risk_Member, consisting of a diagnosis of why the Health_Score is low, a list of recommended events derived from Web_Search_Result entries, and an Outreach_Template.
- **Outreach_Template**: A tailored, ready-to-send draft message, authored by the AI_Agent for the Chapter_Administrator, that the Chapter_Administrator can use to contact the At_Risk_Member.
- **Recommended_Event**: An entry within a Retention_Recommendation, derived from a Web_Search_Result, containing an event title, event date, source URL, and a reason describing why the event fits the Member's interests or Membership_Stage.
- **At_Risk_Threshold**: The configured integer value between 0 and 100 inclusive at or below which a Member's Health_Score causes the Member to be classified as an At_Risk_Member.
- **LLM_Backend**: The external large-language-model service the Query_Interpreter and AI_Agent use for language understanding and synthesis. May be unavailable or exceed its time budget.
- **Deterministic_Query_Parser**: A non-LLM, rule/keyword-based interpreter that maps recognized phrases in a Natural_Language_Query to schema-bounded Interpreted_Criteria without calling the LLM_Backend, used as a fallback when the LLM_Backend is unavailable.
- **Templated_Recommendation**: A Retention_Recommendation whose diagnosis and Outreach_Template are assembled from fixed text templates (not authored by the LLM_Backend), while its Recommended_Event entries are still bound to real Web_Search_Result entries; used as a fallback when the LLM_Backend is unavailable but the web search succeeded.

## Requirements

### Requirement 1: Member Record Management

**User Story:** As a Chapter_Administrator, I want to create and maintain member records with their current stage, so that all member information is stored in one system instead of scattered spreadsheets.

#### Acceptance Criteria

1. WHEN a Chapter_Administrator submits a new member with a non-empty name of 1 to 100 characters and a Membership_Stage equal to one of the allowed values, THE Member_Tracker SHALL create a Member record, assign a system-generated unique identifier that is unique across all existing and previously deleted Member records, and persist the record within 2 seconds.
2. WHEN a Chapter_Administrator updates a Member's Membership_Stage to a different allowed value, THE Member_Tracker SHALL persist the new Membership_Stage, record the change date as a timestamp in UTC, and retain the record's unique identifier unchanged.
3. IF a Chapter_Administrator submits a new member without a name or with a name that is empty or contains only whitespace, THEN THE Member_Tracker SHALL reject the submission, leave all existing Member records unchanged, and return a validation error indicating that the name field is missing or invalid.
4. IF a Chapter_Administrator submits a new member or an update with a Membership_Stage value that is not one of Prospective, Candidate, Inducted, or Inactive, THEN THE Member_Tracker SHALL reject the submission, leave all existing Member records unchanged, and return a validation error indicating that the Membership_Stage value is invalid.
5. THE Member_Tracker SHALL restrict the Membership_Stage value of each Member to exactly one of: Prospective, Candidate, Inducted, Inactive.

### Requirement 2: Age-Out Date Tracking and Alerts

**User Story:** As a Chapter_Administrator, I want automated alerts before a member's age-out date, so that no prospective member lapses without follow-up.

#### Acceptance Criteria

1. WHEN a Chapter_Administrator submits a valid Age_Out_Date for a Member, THE Member_Tracker SHALL store the Age_Out_Date on that Member record, where a valid Age_Out_Date is a well-formed calendar date equal to or later than the current date.
2. IF a Chapter_Administrator submits an Age_Out_Date that is not a well-formed calendar date or is earlier than the current date, THEN THE Member_Tracker SHALL reject the submission, retain the Member's previously stored Age_Out_Date value, and display an error message indicating the date is invalid or past-dated.
3. WHILE the current date is within 30 calendar days before a Member's Age_Out_Date, inclusive of both the 30th day before and the Age_Out_Date itself, THE Member_Tracker SHALL display an age-out alert for that Member on the dashboard showing the whole number of days remaining until the Age_Out_Date.
4. WHEN a Member's Age_Out_Date is earlier than the current date, THE Member_Tracker SHALL flag that Member as aged out on the dashboard and remove the pre-out age-out alert for that Member.
5. IF a Member has no Age_Out_Date set, THEN THE Member_Tracker SHALL display that Member's age-out status as "not applicable" and suppress any age-out alert and aged-out flag for that Member.

### Requirement 3: Attendance Milestone Tracking

**User Story:** As a Chapter_Administrator, I want to record and track attendance against milestones, so that I can see each prospective member's progress toward induction.

#### Acceptance Criteria

1. WHEN a Chapter_Administrator records an Attendance_Record for a Member with an event date on or before the current date, THE Member_Tracker SHALL store the Attendance_Record associated with that Member and confirm the record was saved within 2 seconds.
2. IF a Chapter_Administrator records an Attendance_Record for a Member and event date that duplicates an existing Attendance_Record for the same Member and event date, THEN THE Member_Tracker SHALL reject the Attendance_Record and return a validation error indicating a duplicate attendance entry, retaining the existing Attendance_Record unchanged.
3. WHEN an Attendance_Record is stored for a Member, THE Member_Tracker SHALL display, for that Member, the count of attended events as a non-negative integer and the next unmet Attendance_Milestone, or an indication that all Attendance_Milestones are achieved when no unmet Attendance_Milestone remains.
4. WHEN a Member's attended-event count reaches or exceeds a defined Attendance_Milestone threshold, THE Member_Tracker SHALL mark that Attendance_Milestone as achieved for that Member and display the achieved status.
5. IF a Chapter_Administrator records an Attendance_Record with an event date later than the current date, THEN THE Member_Tracker SHALL reject the Attendance_Record and return a validation error indicating that future-dated attendance is not permitted, without storing the Attendance_Record.

### Requirement 4: Member Health Score

**User Story:** As a Chapter_Administrator, I want a health score for each member, so that I can identify members at risk of dropping out before they leave.

#### Acceptance Criteria

1. THE Member_Tracker SHALL compute a Health_Score as an integer between 0 and 100 inclusive for each Member, where a higher value indicates lower dropout risk.
2. WHEN a Member's Attendance_Record or Membership_Stage changes, THE Member_Tracker SHALL recompute that Member's Health_Score within 5 seconds of the change being persisted.
3. IF a Member's Attendance_Record or Membership_Stage data is missing or incomplete such that a Health_Score cannot be computed, THEN THE Member_Tracker SHALL retain the Member's previous Health_Score, display an indicator that the score is stale, and record the reason the recomputation did not complete.
4. WHILE a Member's Health_Score is at or below the configured at-risk threshold, THE Member_Tracker SHALL classify that Member as an At_Risk_Member and display an at-risk indicator on that Member's dashboard record.
5. THE Member_Tracker SHALL display all At_Risk_Member records grouped together in a single contiguous section on the dashboard.
6. WHERE a Chapter_Administrator configures the at-risk threshold to an integer value between 0 and 100 inclusive, THE Member_Tracker SHALL use the configured value when classifying At_Risk_Member records.
7. IF a Chapter_Administrator submits an at-risk threshold that is non-integer or outside the range 0 to 100 inclusive, THEN THE Member_Tracker SHALL reject the configuration, retain the previously configured threshold, and display an error message indicating the accepted value range.

### Requirement 5: Leadership Handover Data Preservation

**User Story:** As an incoming Chapter_Administrator, I want member data preserved across the annual leadership handover, so that no member history is lost during "One Year to Lead".

#### Acceptance Criteria

1. WHEN a Chapter_Administrator initiates a Leadership_Handover, THE Member_Tracker SHALL create a Handover_Snapshot containing every Member record and every associated Attendance_Record present at the initiation time, and SHALL complete creation within 60 seconds.
2. IF creation of a Handover_Snapshot fails or does not complete within 60 seconds, THEN THE Member_Tracker SHALL discard the incomplete Handover_Snapshot, retain the pre-existing member data unchanged, and return an error indication describing the creation failure.
3. WHEN a Handover_Snapshot is created, THE Member_Tracker SHALL record the creation timestamp in ISO 8601 format including UTC offset, and SHALL retain the Handover_Snapshot for a minimum of 84 months (7 annual handover cycles).
4. WHEN an incoming Chapter_Administrator signs in after a Leadership_Handover, THE Member_Tracker SHALL grant access to all Member records that existed before the Leadership_Handover, such that the count of accessible Member records equals the count captured in the most recent Handover_Snapshot.
5. WHEN a Chapter_Administrator selects a retained Handover_Snapshot, THE Member_Tracker SHALL display all Member records and Attendance_Records contained in that Handover_Snapshot.
6. IF a Chapter_Administrator selects a Handover_Snapshot that is missing or fails an integrity check, THEN THE Member_Tracker SHALL return an error indication that the snapshot is unavailable and SHALL retain all other Handover_Snapshots unchanged.
7. WHEN a Chapter_Administrator exports member data, THE Member_Tracker SHALL produce a file containing every Member record and every associated Attendance_Record, and SHALL complete the export within 60 seconds.
8. IF an export operation fails or does not complete within 60 seconds, THEN THE Member_Tracker SHALL not produce a partial file and SHALL return an error indication describing the export failure.

### Requirement 6: Centralized Dashboard

**User Story:** As a Chapter_Administrator, I want a single dashboard view of all members, so that I can monitor the chapter's growth without consulting multiple spreadsheets.

#### Acceptance Criteria

1. THE Member_Tracker SHALL display a list of all Member records, where each row presents the Member's name, Membership_Stage, age-out status, attendance progress, and Health_Score.
2. WHILE the total count of Member records exceeds 50, THE Member_Tracker SHALL paginate the list into pages of at most 50 records and provide navigation controls to move between pages.
3. WHEN the dashboard is loaded and no Member records exist, THE Member_Tracker SHALL display an empty-state indication conveying that no members are present, rather than a blank list.
4. WHERE a Chapter_Administrator filters the dashboard by Membership_Stage, THE Member_Tracker SHALL display only Member records whose Membership_Stage equals the selected value (Prospective, Candidate, Inducted, or Inactive).
5. WHERE a Chapter_Administrator applies a Membership_Stage filter that matches zero Member records, THE Member_Tracker SHALL display an empty-state indication conveying that no members match the selected filter.
6. WHEN the underlying data for a displayed Member changes and the Chapter_Administrator subsequently loads the dashboard, THE Member_Tracker SHALL display the updated values within 3 seconds of the load request.
7. IF the Member_Tracker cannot retrieve Member records when the dashboard is loaded, THEN THE Member_Tracker SHALL display an error indication conveying that member data is unavailable and SHALL retain the last successfully loaded view without displaying partial or stale records as current.


### Requirement 7: Natural-Language Member Querying

**User Story:** As a Chapter_Administrator, I want to ask plain-English questions about members, so that I can retrieve targeted member sets without composing fixed dashboard filters.

#### Acceptance Criteria

1. WHEN a Chapter_Administrator submits a Natural_Language_Query containing between 1 and 1,000 characters, THE Query_Interpreter SHALL derive Interpreted_Criteria from the Natural_Language_Query and return a Query_Result_Set in which every Member record is an existing Member record drawn from stored data.
2. WHEN the Query_Interpreter returns a Query_Result_Set, THE Member_Tracker SHALL include in the Query_Result_Set only Member records and only field values that are present in the datastore at query time, such that the Query_Result_Set is a subset of the stored Member records and contains no Member record or field value that is absent from the datastore.
3. WHERE a Natural_Language_Query is successfully interpreted, THE Member_Tracker SHALL display the Interpreted_Criteria to the Chapter_Administrator alongside the Query_Result_Set, including each field, comparison, and value applied, so that the Chapter_Administrator can confirm the conditions that were applied.
4. IF the Interpreted_Criteria match zero stored Member records, THEN THE Member_Tracker SHALL return a Query_Result_Set containing zero Member records and display an indication that no members match the Natural_Language_Query.
5. IF the Query_Interpreter cannot derive Interpreted_Criteria from a Natural_Language_Query because the query is ambiguous or uninterpretable, THEN THE Member_Tracker SHALL return a clarification indication stating that the query could not be interpreted and SHALL return zero Member records for that query.
6. IF a Chapter_Administrator submits a Natural_Language_Query that is empty or contains only whitespace characters, THEN THE Member_Tracker SHALL return a clarification indication stating that the query could not be interpreted and SHALL return zero Member records for that query.
7. WHEN a Chapter_Administrator submits a Natural_Language_Query, THE Query_Interpreter SHALL return exactly one of the following outcomes — a non-empty Query_Result_Set, an empty-result indication, or a clarification indication — within 10 seconds of receiving the query, and SHALL NOT return more than one of these outcomes for the same query.
8. IF the Query_Interpreter does not produce an outcome within 10 seconds of receiving the Natural_Language_Query, THEN THE Member_Tracker SHALL return an error indication stating that the query timed out and SHALL return zero Member records for that query.
9. IF the LLM_Backend is unavailable or does not respond within 5 seconds of the Query_Interpreter dispatching the Natural_Language_Query to it, THEN THE Query_Interpreter SHALL derive Interpreted_Criteria using the Deterministic_Query_Parser instead of the LLM_Backend.
10. WHEN the Deterministic_Query_Parser derives Interpreted_Criteria from a Natural_Language_Query, THE Member_Tracker SHALL return a Query_Result_Set that is a subset of the stored Member records containing no Member record or field value that is absent from the datastore, and SHALL display an indication to the Chapter_Administrator that the Query_Result_Set was produced by the Deterministic_Query_Parser fallback.
11. IF neither the LLM_Backend nor the Deterministic_Query_Parser can derive Interpreted_Criteria from the Natural_Language_Query, THEN THE Member_Tracker SHALL return a clarification indication stating that the query could not be interpreted and SHALL return zero Member records for that query.
12. WHILE the Query_Interpreter derives Interpreted_Criteria using the Deterministic_Query_Parser fallback, THE Query_Interpreter SHALL return exactly one of the following outcomes — a non-empty Query_Result_Set, an empty-result indication, or a clarification indication — within 10 seconds of the Chapter_Administrator submitting the Natural_Language_Query, and SHALL NOT return more than one of these outcomes for the same query.

### Requirement 8: AI-Powered Member Retention & Seminar Recommendations

**User Story:** As a Chapter_Administrator, I want an AI agent (Wellington the Wise) to analyze low member health scores and search for relevant local events and seminars, so that I can provide personalized recommendations to re-engage prospective members before they drop out.

#### Acceptance Criteria

1. WHEN a Member's Health_Score drops to or below the At_Risk_Threshold, THE Member_Tracker SHALL flag that Member as an At_Risk_Member eligible for an AI_Retention_Intervention and display an intervention-eligible indicator on that Member's dashboard record.
2. IF a Chapter_Administrator requests an AI_Retention_Intervention for a Member that is not currently flagged as an At_Risk_Member, THEN THE AI_Agent SHALL reject the request and return an error indication stating that the Member is not eligible, without issuing a web search and without producing a Retention_Recommendation.
3. WHEN a Chapter_Administrator requests an AI_Retention_Intervention for an At_Risk_Member, THE AI_Agent SHALL issue a web search for upcoming local seminars, networking events, and skill workshops matching that At_Risk_Member's recorded interests or Membership_Stage, restricted to events with an event date on or after the request date and no more than 30 days after the request date.
4. WHEN a Chapter_Administrator requests an AI_Retention_Intervention for an At_Risk_Member, THE AI_Agent SHALL return exactly one of the following mutually exclusive outcomes within 30 seconds of receiving the request: a Retention_Recommendation, a no-events indication, or an error indication.
5. WHEN the web search returns one or more Web_Search_Result entries for an At_Risk_Member, THE AI_Agent SHALL synthesize a Retention_Recommendation, authored as Wellington the Wise, in a structured form containing exactly three parts: a diagnosis stating why that At_Risk_Member's Health_Score is low, a list of 1 to 3 Recommended_Event entries, and an Outreach_Template.
6. THE AI_Agent SHALL populate each Recommended_Event in a Retention_Recommendation with an event title, an event date, a source URL, and a reason for fit, where the event title, event date, and source URL are drawn from a Web_Search_Result returned for that At_Risk_Member, each Recommended_Event corresponds to a distinct returned Web_Search_Result, each event date is on or after the request date and no more than 30 days after the request date, and no Recommended_Event is fabricated.
7. IF the web search returns zero Web_Search_Result entries for an At_Risk_Member, THEN THE AI_Agent SHALL return a no-events indication stating that no relevant events were found and SHALL return zero Recommended_Event entries and no Retention_Recommendation for that At_Risk_Member.
8. IF the web search or the AI_Agent synthesis fails or does not complete within 30 seconds of the request, THEN THE AI_Agent SHALL return an error indication describing the failure or timeout and SHALL return no partial Retention_Recommendation and no Recommended_Event entries for that At_Risk_Member.
9. WHEN a Chapter_Administrator views an At_Risk_Member record for which a Retention_Recommendation exists, THE Member_Tracker SHALL display the Retention_Recommendation together with an action to copy the Outreach_Template and an action to mark the Retention_Recommendation as sent.
10. WHEN a Chapter_Administrator marks a Retention_Recommendation as sent, THE Member_Tracker SHALL record the sent status with a timestamp in UTC and display the sent status on that At_Risk_Member's record.
11. IF the web search returns one or more Web_Search_Result entries for an At_Risk_Member but the LLM_Backend is unavailable or does not complete synthesis within 30 seconds of the request, THEN THE AI_Agent SHALL produce a Templated_Recommendation containing exactly three parts — a diagnosis assembled from a fixed template, a list of 1 to 3 Recommended_Event entries, and an Outreach_Template assembled from a fixed template — where each Recommended_Event is drawn from a distinct returned Web_Search_Result, has an event date on or after the request date and no more than 30 days after the request date, and is not fabricated, and THE Member_Tracker SHALL display a fallback-mode indicator identifying the recommendation as template-generated.
12. WHILE the AI_Agent operates in fallback (template) mode, THE AI_Agent SHALL produce a Templated_Recommendation only when the web search returned one or more Web_Search_Result entries, SHALL apply the eligibility guard of criterion 2, and SHALL return a no-events indication when the web search returned zero Web_Search_Result entries.
13. IF the LLM_Backend is unavailable and the web search returns zero Web_Search_Result entries, THEN THE AI_Agent SHALL return a no-events indication and SHALL return no Retention_Recommendation, no Templated_Recommendation, and no Recommended_Event entries for that At_Risk_Member; IF the LLM_Backend is unavailable and the web search fails, THEN THE AI_Agent SHALL return an error indication describing the failure and SHALL return no Retention_Recommendation, no Templated_Recommendation, and no Recommended_Event entries for that At_Risk_Member.
14. WHILE the AI_Agent operates in fallback (template) mode, WHEN a Chapter_Administrator requests an AI_Retention_Intervention for an At_Risk_Member, THE AI_Agent SHALL return exactly one of the following mutually exclusive outcomes within 30 seconds of receiving the request — a Templated_Recommendation, a no-events indication, or an error indication — and SHALL NOT return more than one of these outcomes for the same request.
