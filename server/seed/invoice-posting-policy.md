---
id: invoice-posting-policy
path: finance/policies/invoice-posting
title: Invoice Posting Policy
authority_level: policy
owner: finance-ops
tags: ap, invoices, posting, finance
status: published
---
# Invoice Posting Policy

How inbound vendor invoices are posted to the AP ledger, and when a human
reviews first.

## Automated posting

The accounts-payable flow reads this section as governed values. Edits here
change what the flow does — no code changes, and every edit is logged.

- Auto-post limit: $0.00 per invoice. At or under this limit, a confidently
  extracted invoice from a resolved vendor posts without review. **At $0.00
  nothing clears it**, so every inbound invoice raises a review task and a
  person sees it before it posts.
- Minimum extraction confidence: 0.75. Below this the invoice is elevated to
  the ap-clerk queue regardless of amount.
- A vendor the resolver could not settle is always reviewed: the run does not
  assert an identity it could not settle, and the candidates it scored travel
  into the same review as the fields, so the reviewer names the vendor and
  answers the invoice in one sitting rather than at two gates.
- **An invoice with no total, or a total of $0.00, never posts without a
  person.** Not because of the limit above — arithmetically nothing is at or
  under $0.00 either — but because a zero total is what an unread page looks
  like, and paying nothing against a real invoice is a miss rather than a
  saving.

### Why the limit is $0.00, and what changing it does

This is a **decision, taken here rather than in code**, and it is the reason
the limit is a governed value at all. Review every invoice while the desk is
learning what the extraction gets right: the field verdicts tell a clerk which
three numbers to check rather than making them re-read the page, so the cost of
looking at all of them is minutes a day, and what it buys is the correction set
below — the record of where the machine is actually wrong.

Raise it and auto-posting resumes the same afternoon, for every invoice at or
under the new figure, with no deploy and no code review. Nothing else has to
change: the rule that reads this number, the perfect-match allowance on flow
version 7, and the review screen are all shipped and all still work. That is
the whole argument for governed knowledge — the policy moved, the software did
not.

## Approval routing

Which desk a held invoice is reviewed at. Governed here rather than pinned in
code, for the same reason the limit above is: who checks an invoice is a
finance decision, and moving it should be an edit to this page.

| Invoice amount | Reviewed at |
|---|---|
| Over $0.00 | submitter |

Fallback desk: ap-clerk

`submitter` is a resolver, not a desk: it means **the person who handed the
invoice over**, on their own desk. Somebody who uploads an invoice they are
holding is the one person who can say whether the reading matches the page in
front of them, so the review goes back to them rather than to a shared queue
where it waits for a clerk to re-open a document they never saw.

An invoice nobody handed over routes to the fallback desk, and that is the
normal case rather than a failure: the invoices `ap-intake` fetches out of the
customer's document system arrive from a folder, not from a person, so there is
no submitter to route back to. The run records that it fell back and which desk
it landed on, so a deployment quietly routing everything to `ap-clerk` because
no submitter ever reaches the flow is visible instead of invisible.

## Field verdicts

What a reviewer is asked about, rather than being handed the whole document.
The flow checks the extraction against itself before anybody sees it, and every
disagreement names the field it is about, so the review screen can highlight
three numbers on the page instead of thirty.

- Minimum field confidence: 0.70. A field the extractor read less confidently
  than this is put in front of the clerk with a box around it on the page. This
  is per *field* and is a different question from the minimum extraction
  confidence above, which is about the document as a whole: a page can read
  cleanly overall and still have one smudged total.
- Line arithmetic tolerance: $0.01. A billed line whose amount is further than
  this from quantity × unit price is flagged against that line's amount.
- Document arithmetic tolerance: $0.05. The goods lines are checked against the
  subtotal, and subtotal + freight + tax against the total, to within this.
- An invoice missing its vendor, invoice number, invoice date or total is
  flagged as missing that field rather than posted with a blank in it. A
  purchase-order number is deliberately not on that list: plenty of legitimate
  invoices carry none, and the three-way match already has an answer for one
  that does not.
- A due date earlier than the invoice date is flagged. How long the terms are
  is the vendor's business; a due date before the issue date is a misreading.

Widening a tolerance here does not make a wrong invoice right — it decides how
much disagreement is worth a person's attention. Both figures are deliberately
tight: an invoice that disagrees with itself by a nickel was misread somewhere,
and finding out where costs a clerk four seconds.

## The correction set

Every correction a clerk makes is appended to an accumulating dataset of
raw→corrected pairs — what the model read, what the human said instead, on
which document, decided by whom. It is appended automatically and the append is
logged.

**Nothing is promoted automatically.** A pattern in that set is evidence for
revising the Invoice Extraction Prompt; the revision itself is a human decision,
made against this corpus, with a revision number and an author — never an edit
the system makes to its own instructions because it noticed something.

## Three-way match

The flow matches invoice lines against the purchase order they bill and the
receiving report that booked the goods in. These values govern that match.

- Price variance tolerance: 2.0%. A unit price within this of the PO line is
  treated as agreeing.
- Quantity variance tolerance: 0.0 units. Billing more than was received is
  always a variance.
- Freight and tax allowance: $150.00. Lines of kind freight or tax carry no PO
  line by nature; up to this amount they are accepted without one.
- Receiving report required above: $500.00. Below this an invoice may post
  against the PO alone; at or above it, goods must be booked in first.
- Unmatched line tolerance: 0 lines. Any goods line with no PO line is a
  variance a clerk rules on.

## Anomaly screening

Scored against the vendor's own history rather than a global rule, because
"unusual for this vendor" is the only definition that survives contact with a
real vendor list.

- Amount outlier multiple: 3.0. An invoice more than this multiple of the
  vendor's median invoice is flagged.
- First-time-large threshold: $5,000.00. A vendor with fewer than three prior
  invoices billing at least this much is flagged regardless of the multiple.
- Duplicate window: 30 days. A same-vendor invoice within this window whose
  total matches an existing one to the cent is flagged as a suspected
  duplicate.
- Cadence deviation multiple: 4.0. An invoice arriving far outside the
  vendor's usual interval is flagged.
- Anomaly profile window: 365 days. How far back the vendor profile looks.

## Disputes

A rejected invoice is returned to the vendor contact on file with the
clerk's note attached. Recurring rejections from one vendor escalate to the
vendor-management review.
