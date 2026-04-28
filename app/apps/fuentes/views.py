from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import SourceForm
from .models import Source


@login_required
def source_list(request):
    items = Source.objects.order_by("name")
    return render(
        request,
        "fuentes/source_list.html",
        {
            "items": items,
        },
    )


@login_required
def source_create(request):
    if request.method == "POST":
        form = SourceForm(request.POST)
        if form.is_valid():
            obj = form.save()
            messages.success(request, f'Fuente creada: "{obj.name}".')
            return redirect("source_list")
    else:
        form = SourceForm()

    return render(
        request,
        "fuentes/source_form.html",
        {
            "form": form,
            "item": None,
        },
    )


@login_required
def source_edit(request, pk):
    item = get_object_or_404(Source, pk=pk)

    if request.method == "POST":
        form = SourceForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f'Fuente actualizada: "{item.name}".')
            return redirect("source_list")
    else:
        form = SourceForm(instance=item)

    return render(
        request,
        "fuentes/source_form.html",
        {
            "form": form,
            "item": item,
        },
    )