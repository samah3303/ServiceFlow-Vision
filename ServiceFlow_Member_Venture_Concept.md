# A personal agent for the places you go

Venture concept v0.1 · 5 September 2026

Status: exploratory proposal, not implemented functionality. Working description only; no name or trademark clearance performed. The venture-lab framework informed the narrow first use case, economic model and proposed experiment gates.

## The ambition

Give a person a useful, portable memory of their interactions with participating businesses, plus an agent that uses that memory under their direction to find better experiences, relevant products and earned benefits.

Member promise: “Your preferences travel with you. Your history stays under your control. You choose the introductions.”

The long-term hypothesis is that a member-authorised agent can become a trusted intermediary between people and businesses: the person expresses what they want, businesses submit relevant proposals, and the person decides what to accept. Recognition is an optional input. The value has to survive without it.

## An illustrative member experience

These are proposed experiences, not claims about current capabilities.

1. A member says they enjoy exploring savoury snacks and prefer quiet seating. They enable visit logging for selected venues and leave purchase personalisation off until they choose to link a receipt.
2. At a participating café, they check in and authorise sharing their seating preference for this visit. The café receives that instruction and a venue-specific member identifier.
3. A food brand offers a sample relevant to an explicitly selected interest. The app explains the match and labels the invitation as sponsored. The member accepts a pickup location.
4. Staff scan a single-use redemption code when handing over the sample. The app records the handover as verified; it does not infer collection from camera proximity.
5. The member can give feedback, including “not for me,” without losing already-earned benefits. A later purchase is linked only if an authorised transaction source supplies evidence.
6. The private timeline records confirmed visits, receipts, discoveries, rewards and corrections. It distinguishes observed, member-confirmed, merchant-confirmed and inferred information.

A general-purpose visitor must receive a useful benefit before being asked to maintain a detailed profile. A history dashboard alone is unlikely to create sustained use; this remains a hypothesis to test.

## Three products, with separate responsibilities

### Member app

- Private timeline of deliberately enabled visit and transaction records.
- Explicit preferences, current intentions and category exclusions.
- Invitations, sample collection and reward status.
- A clear explanation of each recommendation and any sponsorship.
- Per-venue and per-purpose controls; pause, export, dispute and deletion functions.
- Optional recognition settings separate from loyalty, receipt linking and model-training permissions.

### Venue app

- Check-in and redemption verification.
- Only the member-approved service instructions needed for the current visit.
- Venue-scoped relationship history where authorised.
- Stock and collection workflows, including unavailable samples and failed pickups.
- Existing ServiceFlow operational analytics remain a separate purpose and data flow.

### Brand portal

- Define a campaign, available stock, eligible explicit interests, locations, frequency caps and fulfilment limits.
- Fund sample distribution and review suitably aggregated campaign outcomes.
- Receive voluntary feedback under the permission shown to the member.
- No ability to browse identifiable cross-venue journeys or search for customers by face.

## The first commercial use case

Start with relevant food samples at a small group of nearby cafés or restaurants. Members get an invitation they understand and a convenient place to collect it. Venues host collection and may be compensated for verified fulfilment. Brands pay to run and measure a campaign.

The first product must answer one question: will members repeatedly use a controlled discovery service, and will brands pay enough for a repeat campaign to cover delivery and support?

Expansion to retail, fashion or other categories is contingent on repeat behaviour in the first category. Do not use health, religious, financial or other sensitive inferences as campaign selectors. Dietary or ingredient exclusions should come from explicit member choices and verified product information; they are not medical safety guarantees.

## What may be distinctive

Receipt-linked rewards already exist: Fetch describes earning rewards from receipts and purchase offers. Personal control of data is also an established design direction: Solid describes personal data stores with permissioned access.

Our proposed differentiation is the combination of a member-directed preference history, permissioned service at venues, physical sample fulfilment, and measurable outcomes across participating businesses. This is a hypothesis, not a verified claim of novelty or defensibility.

Sources checked 5 September 2026:

- Fetch: https://fetch.com/faq
- Solid: https://solidproject.org/about
- Solid's explanation of access-control limitations, including downstream copying: https://solidproject.org/faq

These references support the adjacent capabilities only. They do not prove consumer demand, market size or superiority of this proposal.

## Business model and incentive design

Initial payer: the brand running a campaign. Proposed charging basis: a campaign setup/service fee and a charge per verified redemption. The contract must define who supplies samples, pays venue handling and absorbs failed fulfilment. Do not set rates before collecting partner quotes and willingness-to-pay evidence.

Campaign contribution equals revenue minus sample costs borne by us, fulfilment and venue fees, incentives, payment fees, fraud losses, variable infrastructure and support. Measure founder/manual handling time as an operating cost rather than treating it as free.

Later possibilities include venue subscriptions for useful member services and an optional consumer subscription, but neither is assumed in the pilot economics. Selling identifiable movement histories is outside this concept.

Member trust conflicts with paid promotion unless incentives are explicit. Sponsored offers must be labelled. Member relevance and exclusions govern eligibility; payment must not override them. Members choose whether to accept. Brands receive accurate outcome definitions, not claimed causal sales lift from an uncontrolled before/after comparison.

## Data and recognition design

The proposed architecture has four separately permissioned components: member preferences and records; an optional biometric identity service; venue operations; and campaign/redemption accounting.

An outlet sees a venue-scoped identifier. Only the authorised member service resolves cross-venue records for the member. Campaign rules are evaluated within that authorised service, with minimal eligibility results released. “Only the member sees the dashboard” does not mean the operator cannot technically access data: encryption, access controls, auditability and processor responsibilities must substantiate the actual claim.

Use member-initiated QR check-in for the first live consumer experiment. Evaluate optional face verification with consenting adult actors independently. Recognition from general CCTV raises a separate nonmember-processing problem and is not covered by enrolled members' consent. Do not silently convert existing anonymous CCTV records into permanent loyalty profiles.

Face recognition cannot establish bill ownership, unique membership, or sample handover by itself. Receipt-specific linking, single-use redemption records, dispute handling and duplicate-account controls are separate requirements. Model feedback must not automatically become a training dataset.

Revocation stops future matching and sharing and initiates documented deletion across affected stores. Short-lived edge permissions and expiring caches can limit stale recognition; offline locations must fail closed after expiry. Previously disclosed data cannot be magically recalled, so minimise disclosure and specify downstream obligations. Required transaction retention must be explained separately from deletion of biometric templates and optional history.

Jurisdiction-specific privacy review and a defined controller/processor structure are launch dependencies for real-member biometric processing. The initial adult-only pilot avoids introducing a child-member workflow.

## Relationship to the current build

Current ServiceFlow provides recorded-video analysis, table mapping, temporary customer identities, enrolled staff recognition, review/correction, and manager/staff interfaces. It does not provide consumer membership, loyalty accounting, cross-brand consent, live POS integrations or a production identity service.

Reusable elements include venue layout, evidence review and operational task concepts. The member service needs its own authenticated accounts, durable event and consent records, venue scoping and redemption ledger. A production member network should not be added by simply persisting the prototype's temporary customer IDs.

First build deliverable: an isolated demonstration containing a member app, venue redemption view and brand campaign view, using synthetic records and a single scenario from invitation through correction/deletion. This demonstrates the complete proposed experience without claiming live integrations or biometric accuracy.

## Proposed six-week experiment

All counts and thresholds below are proposed decision rules, not benchmarks, forecasts or commitments from partners.

- Decision unlocked: whether to invest in a production member service and deeper POS integration.
- Participants: 100 consenting adult members, three nearby venues and two sample campaigns in one food category.
- Setup: invitations based on explicit interests, member-initiated check-in, single-use collection codes, optional receipt linking, visible permissions and a private activity history.
- Comparison: offer matched groups either preference-based invitations or a general eligible invitation list, holding reward value and operational availability comparable. With this sample, treat results as directional rather than statistically decisive.
- Commitment: brands pay for the pilot; venues name a fulfilment owner; members choose whether to return after their first redemption.
- Time cap: six weeks after venues and stock are ready. Cost cap must be fixed after fulfilment quotes and before recruiting; no spend is authorised by this brief.
- Primary member gate: at least 30% of first-time redeemers voluntarily complete a second discovery/redemption on another day within 30 days, without a larger repeat-only incentive.
- Brand gate: at least one of the two participating brands purchases a follow-on campaign at terms covering measured variable costs.
- Operating gate: at least 95% of accepted collection attempts succeed, with disputes and support minutes reported alongside the rate.
- Control gate: members can explain who sees their history, and pilot exercises demonstrate that pause, correction and deletion behave as promised. Any cross-account receipt or history disclosure blocks expansion until resolved.
- Reframe rule: if fewer than 15% repeat despite reliable availability, or neither brand pays to repeat, revise the value proposition before adding categories or passive recognition. Results between the member thresholds justify one focused retest after diagnosing friction.
- Owner and start date: not assigned. Athul is the proposed decision sponsor, subject to confirming a pilot operator and approved cost cap.

Measure invitation acceptance, actual collection, repeat engagement, feedback, verified purchases where available, campaign contribution and venue workload. Count unknown outcomes as unknown. A redeemed sample is not evidence of consumption; a later purchase is not automatically caused by the sample.

## What could become an advantage

Potential advantages are reliable venue fulfilment, member trust demonstrated through behaviour, explicit and corrected preference histories, and paid campaign outcomes that improve matching. None has been established yet. The existing ServiceFlow prototype gives an engineering starting point; it does not establish distribution or defensibility.

The largest risks are weak repeat utility, dependence on subsidies, expensive venue operations, incomplete POS coverage, attribution errors, paid-promotion conflicts and difficult acquisition on all three sides. A smaller successful discovery service is preferable to prematurely funding a universal cross-category network.

## Decision

Test the member-controlled discovery and sampling proposition. Prepare the complete synthetic product experience before committing to production recognition or broad venue deployment. Advance only with evidence of member repeat, paid brand repeat and workable fulfilment economics.
