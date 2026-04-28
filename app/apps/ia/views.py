from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AIProviderConfigForm
from .models import AIProviderConfig


@login_required
def ai_provider_list(request):
    items = AIProviderConfig.objects.all().order_by("priority_order", "name")
    return render(
        request,
        "ia/provider_list.html",
        {
            "items": items,
        },
    )


@login_required
def ai_provider_create(request):
    if request.method == "POST":
        form = AIProviderConfigForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, "Proveedor IA creado correctamente.")
            return redirect("ai_provider_edit", pk=obj.pk)
    else:
        form = AIProviderConfigForm()

    return render(
        request,
        "ia/provider_form.html",
        {
            "form": form,
            "section_title": "Nuevo proveedor IA",
            "item": None,
        },
    )


@login_required
def ai_provider_edit(request, pk):
    item = get_object_or_404(AIProviderConfig, pk=pk)

    if request.method == "POST":
        form = AIProviderConfigForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Proveedor IA actualizado correctamente.")
            return redirect("ai_provider_edit", pk=item.pk)
    else:
        form = AIProviderConfigForm(instance=item)

    return render(
        request,
        "ia/provider_form.html",
        {
            "form": form,
            "section_title": "Editar proveedor IA",
            "item": item,
        },
    )