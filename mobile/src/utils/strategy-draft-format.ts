// Shared text <-> structured-field conversion for the strategy draft flow.
// consensus/triggers are edited as raw JSON object text, not "key: value"
// lines - a line-based, colon-split format can never be genuinely lossless
// for arbitrary keys, since nothing stops a real metric/trigger name from
// containing a colon itself (e.g. "Revenue: FY27"), which a naive
// `line.indexOf(':')` split can't tell apart from the key/value separator.
// JSON is the structured representation with no such ambiguity: a key is
// always exactly the JSON string it was written as, whatever characters it
// contains, and a value's type (string/number/null) is explicit in its
// syntax rather than guessed from its shape.

import { EventExpectation, StrategyDraftInput } from '@/services/api';

export type DraftFormState = {
  instrument: string;
  eventName: string;
  scheduledDate: string;
  consensusText: string;
  kpiText: string;
  bullText: string;
  baseText: string;
  bearText: string;
  triggersText: string;
  invalidationText: string;
  sourceName: string;
  sourceUrl: string;
  sourceAsOf: string;
  changeNote: string;
  summary: string;
  assumptionsText: string;
  unresolvedText: string;
};

export function listToText(items: string[]): string {
  return items.join('\n');
}

export function textToList(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

// Pretty-printed JSON object literal - the single source of truth for how
// a consensus/triggers record is represented as editable text. Every key
// and value round-trips through JSON.stringify/JSON.parse exactly: keys
// keep any character they contain (including ":"), strings stay strings
// (quoted), numbers stay numbers, and null stays null.
export function recordToText(record: Record<string, unknown>): string {
  return JSON.stringify(record, null, 2);
}

export type TypedRecordParseResult =
  | { ok: true; value: Record<string, number | string | null> }
  | { ok: false; error: string };

// The one parser for consensus/triggers text. Never guesses or silently
// normalizes: invalid JSON, a non-object top level, or a value that isn't
// string/number/null is reported back as an explicit error for the caller
// to show the user, not coerced into "something reasonable."
export function parseTypedRecord(text: string): TypedRecordParseResult {
  const trimmed = text.trim();
  if (trimmed.length === 0) {
    return { ok: true, value: {} };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Tuntematon JSON-virhe';
    return { ok: false, error: `Virheellinen JSON: ${message}` };
  }

  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {
      ok: false,
      error: 'Arvon täytyy olla JSON-objekti, esim. {"avain": 1.0}',
    };
  }

  // Built via Object.fromEntries() below, never `result[key] = value` on a
  // plain {} - a key literally named "__proto__" is a normal, valid JSON
  // object key (JSON.parse itself creates it as a real own property, not
  // through the accessor), but assigning to it with `obj[key] = value`
  // goes through Object.prototype's __proto__ *setter*, which silently
  // discards the value entirely for a non-object value like a string,
  // number, or null - losing that key/value pair rather than storing it.
  // Object.fromEntries uses CreateDataProperty semantics, so it creates a
  // genuine own property named "__proto__" instead, exactly like every
  // other key.
  const entries: [string, number | string | null][] = [];
  for (const [key, raw] of Object.entries(parsed as Record<string, unknown>)) {
    if (raw !== null && typeof raw !== 'number' && typeof raw !== 'string') {
      return {
        ok: false,
        error: `Avaimen "${key}" arvon täytyy olla numero, merkkijono tai null (sai: ${typeof raw}).`,
      };
    }
    // A JSON number literal can be syntactically valid but overflow to
    // Infinity (e.g. "1e400") - JSON itself has no way to write NaN or
    // literal Infinity/-Infinity tokens, so this overflow case is the only
    // way a non-finite number reaches here. It must be rejected as a
    // validation error, never silently accepted: JSON.stringify(Infinity)
    // renders as `null`, so an accepted Infinity would quietly turn into a
    // real null the next time this record round-trips through recordToText
    // - a value the user never actually entered.
    if (typeof raw === 'number' && !Number.isFinite(raw)) {
      return {
        ok: false,
        error: `Avaimen "${key}" arvo ei ole äärellinen luku (sai: ${raw}).`,
      };
    }
    entries.push([key, raw]);
  }
  return { ok: true, value: Object.fromEntries(entries) };
}

// Convenience for callers (draftFormToInput) that need a value or a thrown
// error, never a silently guessed/normalized fallback - invalid consensus/
// triggers text must stop a preview/approve attempt, not send something
// "close enough" to the backend.
export function textToTypedRecord(text: string): Record<string, number | string | null> {
  const result = parseTypedRecord(text);
  if (!result.ok) {
    throw new Error(result.error);
  }
  return result.value;
}

export function draftFormFromEvent(event: EventExpectation): DraftFormState {
  return {
    instrument: event.instrument,
    eventName: event.event_name,
    scheduledDate: event.scheduled_date,
    consensusText: recordToText(event.consensus),
    kpiText: listToText(event.important_kpis),
    bullText: listToText(event.bull_case),
    baseText: listToText(event.base_case),
    bearText: listToText(event.bear_case),
    triggersText: recordToText(event.triggers),
    invalidationText: listToText(event.invalidation_conditions),
    sourceName: event.source_name ?? '',
    sourceUrl: event.source_url ?? '',
    sourceAsOf: event.source_as_of ?? '',
    changeNote: '',
    summary: '',
    assumptionsText: '',
    unresolvedText: '',
  };
}

export function draftFormToInput(draft: DraftFormState): StrategyDraftInput {
  return {
    instrument: draft.instrument,
    event_name: draft.eventName,
    scheduled_date: draft.scheduledDate,
    consensus: textToTypedRecord(draft.consensusText),
    important_kpis: textToList(draft.kpiText),
    bull_case: textToList(draft.bullText),
    base_case: textToList(draft.baseText),
    bear_case: textToList(draft.bearText),
    triggers: textToTypedRecord(draft.triggersText),
    invalidation_conditions: textToList(draft.invalidationText),
    source_name: draft.sourceName.trim() || null,
    source_url: draft.sourceUrl.trim() || null,
    source_as_of: draft.sourceAsOf.trim() || null,
    change_note: draft.changeNote,
    summary: draft.summary,
    assumptions: textToList(draft.assumptionsText),
    unresolved_questions: textToList(draft.unresolvedText),
  };
}
