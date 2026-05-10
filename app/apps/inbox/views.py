from django.contrib import messages
from django.core.management import call_command
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.busquedas.models import SearchProfile
from apps.inmuebles.models import CapturedProperty

from .forms import EmailAccountForm, InboundEmailConvertToCaptureForm
from .models import EmailAccount, InboundEmail


@login_required
def inbox_sync(request):
    if request.method != "POST":
        return redirect("inbox_list")

    accounts = EmailAccount.objects.filter(owner=request.user, is_active=True)

    if not accounts.exists():
        messages.error(request, "No hay cuentas activas para sincronizar.")
        return redirect("inbox_list")

    for account in accounts:
        call_command(
            "sync_inbox_email",
            account_id=account.id,
            limit=30,
            update_existing=True,
        )

    messages.success(request, "Bandeja actualizada correctamente.")
    return redirect("inbox_list")


@login_required
def inbox_list(request):
    status = request.GET.get("status", "").strip()
    search_profile_id = request.GET.get("search_profile_id", "").strip()

    qs = (
        InboundEmail.objects
        .select_related("account", "search_profile", "captured_property")
        .filter(owner=request.user)
        .order_by("-received_at", "-created_at")
    )


    if status == "all":
        pass
    elif status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(status=InboundEmail.Status.NEW)

    if search_profile_id:
        qs = qs.filter(search_profile_id=search_profile_id)

    available_search_profiles = SearchProfile.objects.filter(owner=request.user).order_by("status", "name")

    return render(
        request,
        "inbox/inbox_list.html",
        {
            "items": qs,
            "current_status": status,
            "current_search_profile_id": search_profile_id,
            "status_choices": InboundEmail.Status.choices,
            "available_search_profiles": available_search_profiles,
        },
    )


@login_required
def inbox_detail(request, pk):
    item = get_object_or_404(
        InboundEmail.objects.select_related("account", "search_profile", "captured_property"),
        pk=pk,
        owner=request.user,
    )

    convert_form = InboundEmailConvertToCaptureForm(
        user=request.user,
        inbound_email=item,
    )

    return render(
        request,
        "inbox/inbox_detail.html",
        {
            "item": item,
            "convert_form": convert_form,
        },
    )


@login_required
def email_account_list_create(request):
    items = EmailAccount.objects.filter(owner=request.user).order_by("name")

    if request.method == "POST":
        form = EmailAccountForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.owner = request.user
            obj.save()
            messages.success(request, f'Cuenta de email creada: "{obj.name}".')
            return redirect("email_account_list")
    else:
        form = EmailAccountForm()

    return render(
        request,
        "inbox/email_account_list.html",
        {
            "items": items,
            "form": form,
        },
    )


@login_required
def inbox_convert_to_capture(request, pk):
    item = get_object_or_404(
        InboundEmail.objects.select_related("account", "search_profile", "captured_property"),
        pk=pk,
        owner=request.user,
    )

    if request.method != "POST":
        return redirect("inbox_detail", pk=item.pk)

    if item.captured_property_id:
        messages.info(request, "Este email ya tiene una captación asociada.")
        return redirect("capturedproperty_detail", pk=item.captured_property_id)

    form = InboundEmailConvertToCaptureForm(
        request.POST,
        user=request.user,
        inbound_email=item,
    )

    if not form.is_valid():
        return render(
            request,
            "inbox/inbox_detail.html",
            {
                "item": item,
                "convert_form": form,
            },
        )

    data = form.cleaned_data

    source_external_id = (
        item.message_id
        or item.message_uid
        or f"email-{item.pk}"
    )

    capture = CapturedProperty.objects.create(
        owner=request.user,
        search_profile=data["search_profile"],
        source=data["source"],
        entry_mode=CapturedProperty.EntryMode.EMAIL,
        operation_type=data["operation_type"],
        property_type=data["property_type"],
        title=data["title"],
        source_url=data["source_url"] or "",
        source_external_id=source_external_id,
        price=data["price"],
        province=data["province"],
        municipality=data["municipality"],
        zone_text=data["zone_text"],
        bedrooms=data["bedrooms"],
        bathrooms=data["bathrooms"],
        area_m2=data["area_m2"],
        description_raw=data["description_raw"],
        manual_notes=data["manual_notes"],
        status=CapturedProperty.Status.CAPTURED,
        review_status=CapturedProperty.ReviewStatus.PENDING,
    )

    item.search_profile = data["search_profile"]
    item.captured_property = capture
    item.status = InboundEmail.Status.CONVERTED
    item.save(update_fields=[
        "search_profile",
        "captured_property",
        "status",
        "updated_at",
    ])

    messages.success(request, f'Captación creada desde email: "{capture.title}".')
    return redirect("capturedproperty_detail", pk=capture.pk)


@login_required
def inbox_discard(request, pk):
    item = get_object_or_404(InboundEmail, pk=pk, owner=request.user)

    if request.method != "POST":
        return redirect("inbox_detail", pk=item.pk)

    if item.status != InboundEmail.Status.CONVERTED:
        item.status = InboundEmail.Status.DISCARDED
        item.save(update_fields=["status", "updated_at"])
        messages.success(request, "Email descartado correctamente.")
    else:
        messages.info(request, "Este email ya está convertido en captación y no se descarta.")

    return redirect("inbox_list")
