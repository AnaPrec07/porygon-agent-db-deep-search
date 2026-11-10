/* Pure helpers for the plan-review card and the research timeline (PR-9).
 * Loaded before app.js. Also importable under Node for tests. */
(function (root) {
  "use strict";

  const TITLES = {
    intake: "Starting research",
    memory_retriever: "Checking prior research",
    plan_generator: "Drafting the research plan",
    plan_review: "Waiting for plan approval",
    section_planner: "Outlining the report",
    section_researcher: "Researching",
    research_evaluator: "Evaluating findings",
    enhanced_search_executor: "Refining research",
    report_composer_with_citations: "Writing the report",
    citation_processor: "Resolving citations",
    response_finalizer: "Finishing",
    finalize_cancelled: "Cancelled",
  };

  function isPlanReview(interrupt) {
    return interrupt?.value?.type === "plan_review";
  }

  function planText(interrupt) {
    return String(interrupt?.value?.plan || "").trim();
  }

  function approvalLabels(interrupt) {
    return isPlanReview(interrupt)
      ? { approve: "Approve plan", reject: "Cancel research" }
      : { approve: "Approve", reject: "Reject" };
  }

  // Resume payloads. The plan graph expects {decision}; the template's tool approvals expect {decisions}.
  function resumePayload(decision, interrupt) {
    if (isPlanReview(interrupt)) {
      return { resume: { decision: decision === "approve" ? "approve" : "cancel" } };
    }
    return decision === "approve"
      ? { resume: { decisions: [{ type: "approve" }] } }
      : { resume: { decisions: [{ type: "reject", message: "Rejected from the Agent Bricks demo UI." }] } };
  }

  function timelineLabel(event) {
    const base = TITLES[event?.agent_name] || event?.title || event?.agent_name || "Step";
    return event?.status === "waiting" ? `${base} (waiting)` : base;
  }

  const api = { TITLES, isPlanReview, planText, approvalLabels, resumePayload, timelineLabel };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.DeepSearchReview = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
