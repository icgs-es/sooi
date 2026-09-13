"""Customer-safe search governance policy and presentation for SR0.3B."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from apps.core.plans import resolve_entitlement
from apps.ia.usage import can_run_ai_discovery

from .adaptive_planner import MODE_POLICIES, coverage_plan, MARKET_COMPLETENESS_DISCLAIMER
from .models import SearchRun


MODE_ORDER = ("free", "eco", "amplia", "profunda")
MODE_UX = {
    "free": {
        "label": "Gratis",
        "description": "Cobertura limitada: fuentes locales/reutilizables y deterministas; sin descubrimiento externo asistido por IA.",
    },
    "eco": {
        "label": "Eco",
        "description": "Fuentes deterministas primero y una expansión externa pequeña y acotada solo si hace falta; puede parar al alcanzar cobertura útil.",
    },
    "amplia": {
        "label": "Amplia",
        "description": "Expansión de fuentes más amplia que Eco, todavía acotada y con posible parada anticipada.",
    },
    "profunda": {
        "label": "Profunda",
        "description": "La búsqueda técnica más amplia disponible actualmente; sigue siendo finita, gobernada y puede parar antes.",
    },
}

# Customer authorization units. They intentionally track current technical caps;
# SR0.3C may calibrate/replace this policy without introducing another ledger.
MODE_AUTHORIZATION = {
    mode: {"default": Decimal(str(policy.max_ai_calls)), "maximum": Decimal(str(policy.max_ai_calls))}
    for mode, policy in MODE_POLICIES.items()
}

COVERAGE_COPY = {
    "full": "Se ejecutó toda la cobertura planificada para este modo finito.",
    "partial": "La cobertura ejecutada fue parcial; puede haber fuentes no recorridas.",
    "limited_by_plan": "Cobertura limitada por el plan o modo autorizado; no se ejecutaron todas las fuentes técnicamente disponibles.",
    "limited_by_budget": "Cobertura limitada por el máximo autorizado: la búsqueda se detuvo antes de ejecutar todas las fuentes previstas.",
    "degraded_provider": "Parte de la cobertura no pudo ejecutarse por una incidencia temporal del servicio.",
    "no_results": "No se encontraron resultados dentro de la cobertura ejecutada. Esto no prueba que el mercado esté vacío.",
    "failed": "La búsqueda no pudo completarse. No se muestran detalles internos del servicio.",
    "not_evaluated": "Esta ejecución histórica no tiene una evaluación de cobertura gobernada.",
}
STOP_REASON_COPY = {
    "none": "Sin motivo de parada evaluado.",
    "sufficient_coverage": "Se alcanzó cobertura útil y la búsqueda se detuvo anticipadamente.",
    "budget_exhausted": "Se alcanzó el máximo autorizado.",
    "provider_hard_failure": "La cobertura se detuvo por una incidencia temporal del servicio.",
    "provider_outage": "Parte del servicio no estaba disponible temporalmente.",
    "no_applicable_sources": "No había fuentes aplicables a estos filtros dentro del modo.",
    "plan_limit": "Se alcanzó el límite del plan autorizado.",
    "user_cancelled": "La búsqueda fue cancelada.",
    "completed_plan": "Se completó el plan técnico previsto para este modo.",
    "failed_internal": "La ejecución se detuvo por una incidencia interna.",
}

PARTIAL_COMPLETION_LABEL = "Completada · cobertura parcial"


def customer_run_status(run):
    """Presentation-only status: controlled partial completion is not an error."""
    status = getattr(run, "status", "")
    error_message = getattr(run, "error_message", "")
    coverage_status = getattr(run, "coverage_status", "")
    stop_reason = getattr(run, "stop_reason", "")
    raw_response = getattr(run, "raw_response", None)
    payload = raw_response if isinstance(raw_response, dict) else {}
    quality = payload.get("quality_semantics") or {}
    true_provider_failures = int(quality.get("actual_provider_failure_count") or 0)
    has_real_error = bool(
        status == SearchRun.Status.FAILED
        or str(error_message or "").strip()
        or true_provider_failures
        or coverage_status == SearchRun.CoverageStatus.DEGRADED_PROVIDER
        or stop_reason in {
            SearchRun.StopReason.PROVIDER_HARD_FAILURE,
            SearchRun.StopReason.PROVIDER_OUTAGE,
            SearchRun.StopReason.FAILED_INTERNAL,
        }
    )
    get_status_display = getattr(run, "get_status_display", None)
    display_status = get_status_display() if callable(get_status_display) else str(status or "")
    if has_real_error:
        return {"label": display_status, "kind": "error"}
    if coverage_status in {
        SearchRun.CoverageStatus.PARTIAL,
        SearchRun.CoverageStatus.LIMITED_BY_PLAN,
        SearchRun.CoverageStatus.LIMITED_BY_BUDGET,
    } or stop_reason == SearchRun.StopReason.BUDGET_EXHAUSTED:
        return {"label": PARTIAL_COMPLETION_LABEL, "kind": "partial"}
    return {"label": display_status, "kind": status}


def ai_discovery_permitted(user):
    entitlement = resolve_entitlement(user)
    if "ai_discovery" not in entitlement.capabilities:
        return False, "Tu plan actual no permite descubrimiento externo asistido."
    allowed, _usage = can_run_ai_discovery(user)
    if not allowed:
        return False, "Tu autorización actual no permite iniciar una expansión externa."
    return True, ""


def validate_authorization(user, mode, budget):
    if mode not in MODE_ORDER or mode not in SearchRun.SearchMode.values:
        raise ValidationError({"search_mode": "Selecciona un modo de búsqueda válido."})
    try:
        value = Decimal(str(budget))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({"budget_max_credits": "El límite autorizado debe ser numérico y finito."})
    maximum = MODE_AUTHORIZATION[mode]["maximum"]
    if not value.is_finite() or value < 0:
        raise ValidationError({"budget_max_credits": "El límite autorizado debe ser finito y no negativo."})
    if value > maximum:
        raise ValidationError({"budget_max_credits": f"El máximo permitido para {MODE_UX[mode]['label']} es {maximum}."})
    if mode != "free":
        allowed, reason = ai_discovery_permitted(user)
        if not allowed:
            raise ValidationError({"search_mode": reason})
    return value


def next_broader_mode(mode):
    try:
        return MODE_ORDER[MODE_ORDER.index(mode) + 1]
    except (ValueError, IndexError):
        return None


def eco_one_credit_display_plan(locations):
    """Presentation projection of the SR0.16H.5 one-credit ECO topology."""
    return coverage_plan(
        locations, Decimal("1"), maximum_calls=1, planned_calls=1,
        consolidate_locations=True,
    )


def mode_previews(profile, user):
    """Pure/provider-independent preflight; it never executes discovery."""
    from .services_hybrid_coverage_v261 import SOURCE_SPECS
    from .adaptive_planner import AdaptivePlanner

    ai_allowed, ai_reason = ai_discovery_permitted(user)
    from .commercial_metering import commercial_metering_enabled, get_commercial_usage_summary
    available_balance = (
        get_commercial_usage_summary(user)["credits_remaining"]
        if commercial_metering_enabled() else None
    )
    previews = []
    for mode in MODE_ORDER:
        policy, copy = MODE_POLICIES[mode], MODE_UX[mode]
        permitted = mode == "free" or ai_allowed
        location_count = max(1, len(profile.canonical_search_locations()))
        planned_calls = AdaptivePlanner(mode, SOURCE_SPECS, location_count=location_count).calls_planned
        plan = coverage_plan(
            profile.canonical_search_locations(), MODE_AUTHORIZATION[mode]["default"],
            maximum_calls=min(4, policy.max_ai_calls),
            planned_calls=planned_calls,
        )
        previews.append({
            "mode": mode, "label": copy["label"], "description": copy["description"],
            "external_ai_may_be_used": policy.max_ai_calls > 0,
            "maximum_external_calls": policy.max_ai_calls,
            "maximum_external_sources": policy.max_ai_sources,
            "default_authorization": MODE_AUTHORIZATION[mode]["default"],
            "maximum_authorization": MODE_AUTHORIZATION[mode]["maximum"],
            "permitted": permitted, "denied_reason": "" if permitted else ai_reason,
            "next_broader_mode": next_broader_mode(mode),
            "coverage_limitation": "El resultado describe solo la cobertura ejecutada; ningún modo garantiza cubrir todo el mercado.",
            "coverage_plan": plan,
            "eco_one_credit_coverage_plan": (
                eco_one_credit_display_plan(profile.canonical_search_locations())
                if mode == "eco" else None
            ),
            "partial_warning": bool(plan["scheduled_units"] < plan["planned_units"] or mode != "free"),
            "available_credit_balance": available_balance,
        })
    return previews


def run_customer_context(run):
    payload = run.raw_response if isinstance(run.raw_response, dict) else {}
    contract = payload.get("coverage_contract") or {}
    quality = payload.get("quality_semantics") or contract.get("quality_semantics") or {}
    if "actionable_count" not in quality and payload.get("source_coverage"):
        from .search_quality_semantics import evaluate_search_quality_semantics
        quality = evaluate_search_quality_semantics(payload)
    accepted = int(quality.get("actionable_count", run.total_valid_candidates or 0))
    review_count = int(quality.get("review_required_count") or 0)
    near_count = int((payload.get("recovery_observability") or {}).get("near_match_candidate_count") or 0)
    if accepted > 0:
        commercial_result_state = "ACTIONABLE_RESULTS_PRESENT"
        commercial_headline = f"{accepted} oportunidades disponibles"
    elif review_count > 0:
        commercial_result_state = "REVIEW_RESULTS_PRESENT"
        commercial_headline = f"{review_count} resultados para revisar"
    elif near_count > 0:
        commercial_result_state = "NEAR_MATCH_RESULTS_PRESENT"
        commercial_headline = "No hay resultados exactos confirmados"
    else:
        commercial_result_state = "NO_ELIGIBLE_RESULTS"
        commercial_headline = "No se encontraron resultados elegibles"
    partial_empty = contract.get("empty_result_classification") == "PARTIAL_EMPTY_RESULT"
    if commercial_result_state == "REVIEW_RESULTS_PRESENT":
        result_summary = (
            f"{commercial_headline}. Se encontraron resultados que cumplen los criterios "
            "de búsqueda, pero requieren verificar su disponibilidad."
        )
    elif commercial_result_state == "NEAR_MATCH_RESULTS_PRESENT":
        result_summary = f"{commercial_headline}: {near_count} alternativas próximas para revisar."
    elif partial_empty:
        result_summary = (
            f"0 resultados en la cobertura ejecutada: {contract.get('executed_units', 0)} de "
            f"{contract.get('planned_units', 0)} ubicaciones ({contract.get('coverage_percent_executed', 0):.2f}%)."
        )
    elif contract.get("empty_result_classification") == "FULL_PLAN_EMPTY_RESULT":
        result_summary = (
            f"0 resultados aceptados tras completar {contract.get('executed_units')} de "
            f"{contract.get('planned_units')} ubicaciones del plan SOOI."
        )
    else:
        result_summary = f"{accepted} resultados aceptados en la cobertura ejecutada."
    return {
        "run": run,
        "customer_status": customer_run_status(run),
        "mode": MODE_UX.get(run.search_mode, {"label": "Histórica", "description": "Ejecución anterior a la gobernanza."}),
        "coverage_text": COVERAGE_COPY.get(run.coverage_status, COVERAGE_COPY["not_evaluated"]),
        "stop_reason_text": STOP_REASON_COPY.get(run.stop_reason, STOP_REASON_COPY["none"]),
        "next_mode": next_broader_mode(run.search_mode),
        "coverage_contract": contract,
        "quality_semantics": quality,
        "result_summary": result_summary,
        "commercial_result_state": commercial_result_state,
        "commercial_headline": commercial_headline,
        "commercial_review_count": review_count,
        "commercial_near_match_count": near_count,
        "result_scope_display": (
            "REVIEW_RESULTS_PRESENT" if commercial_result_state == "REVIEW_RESULTS_PRESENT"
            else contract.get("empty_result_classification")
        ),
        "market_disclaimer": contract.get("market_completeness_disclaimer", MARKET_COMPLETENESS_DISCLAIMER),
        "can_expand": bool(
            run.governance_enabled and next_broader_mode(run.search_mode)
            and (contract.get("remaining_units", 0) > 0 or run.coverage_status in {"partial", "limited_by_plan", "limited_by_budget", "degraded_provider", "no_results"})
        ),
    }


def get_searchrun_list_metrics(run):
    """Return stable, run-scoped counters for the recent-runs list.

    ``total_*`` fields are historical execution counters and may predate the
    review-capture path.  Modern commercial buckets come from the persisted
    quality payload; captures created by a later SR0.16I replay are linked
    directly to this SearchRun and therefore remain auditable without using
    global inbox totals.
    """
    payload = run.raw_response if isinstance(run.raw_response, dict) else {}
    quality = payload.get("quality_semantics") or {}
    if not isinstance(quality, dict):
        quality = {}
    if not quality and payload.get("source_coverage"):
        from .search_quality_semantics import evaluate_search_quality_semantics
        quality = evaluate_search_quality_semantics(payload)

    actionable = int(quality.get("actionable_count") or 0)
    review = int(quality.get("review_required_count") or 0)
    near = int((payload.get("recovery_observability") or {}).get("near_match_candidate_count") or 0)
    found = actionable + review
    if not found:
        # Legacy runs have no modern buckets; preserve their stored value.
        found = max(0, int(getattr(run, "total_found", 0) or 0))

    action_totals = (payload.get("action_plan") or {}).get("totals") or {}
    discarded = int(action_totals.get("skip_discarded") or 0)
    if discarded < 0:
        discarded = 0
    if not discarded and found and not near:
        raw_count = int(quality.get("raw_candidates") or payload.get("total_candidates") or 0)
        discarded = max(0, raw_count - found)
    if not discarded and not quality and not action_totals:
        discarded = max(0, int(getattr(run, "total_errors", 0) or 0))

    created = 0
    try:
        from apps.inmuebles.models import CapturedProperty
        # ``search_run`` is the canonical provenance relation.  Count only
        # captures explicitly linked to this run; never infer ownership from
        # timestamps or from the global inbox.  A zero FK count is meaningful
        # and must not be replaced by ``total_new``.
        run_pk = getattr(run, "pk")
        created = int(CapturedProperty.objects.filter(search_run_id=run_pk).count())
    except Exception:
        # Presentation must remain compatible with historical/non-Django
        # contexts; the persisted run counter is the safe fallback.
        created = max(0, int(getattr(run, "total_new", 0) or 0))

    # A positive FK count is authoritative.  Historical runs may have valid
    # ``total_new`` counters but no search_run links on legacy captures, so
    # retain that value only when the canonical relation yields zero.
    legacy_new = max(0, int(getattr(run, "total_new", 0) or 0))
    new_count = created if created > 0 else legacy_new

    return {
        "found": max(0, found),
        "new": max(0, new_count),
        "updated": max(0, int(getattr(run, "total_updated", 0) or 0)),
        "discarded": max(0, discarded),
    }
