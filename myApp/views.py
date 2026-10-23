from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views import generic
import requests
from django.contrib import messages
from django.core.cache import cache
from django.test.utils import CaptureQueriesContext
from django.db import connection
import logging
import time
from django.http import JsonResponse

from .models import RecipeIngredient, Category, Recipe, Ingredient, Favorite, Rating
from .forms import RatingForm

# Set up logging
logger = logging.getLogger(__name__)

# Counters for metrics
api_calls = 0
cache_hits = 0
db_queries_before_optimization = 0
db_queries_after_optimization = 0


class IndexView(generic.TemplateView):
    template_name = "myApp/index.html"

    def get_context_data(self, **kwargs):
        global api_calls, cache_hits
        context = super().get_context_data(**kwargs)

        # Look for 'random_drink' in the cache
        drink = cache.get("random_drink")

        if not drink:
            start = time.time()
            api_calls += 1

            try:
                response = requests.get(
                    "https://www.thecocktaildb.com/api/json/v1/1/random.php"
                )
                response.raise_for_status()
                data = response.json()
                drink = data["drinks"][0]

                elapsed = (time.time() - start) * 1000  # Convert to ms
                logger.info(
                    f"🔄 API CALL #{api_calls} - Random drink - {elapsed:.2f}ms"
                )

                # Store it for 5 minutes (300 seconds)
                cache.set("random_drink", drink, 300)
            except Exception as e:
                logger.error(f"API Error: {e}")
                messages.warning(self.request, "Check your internet connection.")
                drink = None
        else:
            cache_hits += 1
            logger.info(f"✓ CACHE HIT #{cache_hits} - Random drink (cached)")

        context["drink"] = drink
        context["metrics"] = {"api_calls": api_calls, "cache_hits": cache_hits}
        return context


class SearchView(generic.TemplateView):
    template_name = "myApp/search.html"

    def get(self, request, *args, **kwargs):
        global api_calls, cache_hits

        start_total = time.time()
        query = request.GET.get("query")
        drinks = None
        timing_info = {}

        if query:
            # Create a unique key for every search term
            cache_key = f"search_res_{query.lower().strip()}"
            drinks = cache.get(cache_key)

            if not drinks:
                # API call needed
                start_api = time.time()
                api_calls += 1

                url = (
                    f"https://www.thecocktaildb.com/api/json/v1/1/search.php?s={query}"
                )
                try:
                    response = requests.get(url)
                    response.raise_for_status()
                    drinks = response.json().get("drinks")

                    api_time = (time.time() - start_api) * 1000
                    logger.info(
                        f"🔄 API CALL #{api_calls} - Search '{query}' - {api_time:.2f}ms"
                    )
                    timing_info["api_time_ms"] = api_time

                    if drinks:
                        # Cache successful search results for 1 hour
                        cache.set(cache_key, drinks, 3600)
                    else:
                        drinks = {"error": "No drinks found!"}
                except Exception as e:
                    logger.error(f"API Error during search: {e}")
                    drinks = {"error": "API Error!"}
            else:
                cache_hits += 1
                logger.info(f"✓ CACHE HIT #{cache_hits} - Search '{query}' (cached)")
                timing_info["cached"] = True

        total_time = (time.time() - start_total) * 1000
        timing_info["total_time_ms"] = total_time

        logger.info(f"Total search time: {total_time:.2f}ms")
        print(f"\n📊 SEARCH METRICS:")
        print(f"   Total API Calls: {api_calls}")
        print(f"   Total Cache Hits: {cache_hits}")
        print(f"   Total Time: {total_time:.2f}ms\n")

        return render(
            request,
            self.template_name,
            {"query": query, "drinks": drinks, "timing_info": timing_info},
        )


def add_to_favorite(request, pk):
    global api_calls

    start = time.time()
    api_calls += 1

    url = f"https://www.thecocktaildb.com/api/json/v1/1/lookup.php?i={pk}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        drink = response.json()["drinks"][0]

        api_time = (time.time() - start) * 1000
        logger.info(f"🔄 API CALL #{api_calls} - Lookup drink {pk} - {api_time:.2f}ms")

        if not drink:
            drink = {"error": "No drinks found for this search!"}
    except Exception as e:
        logger.error(f"API Error in add_to_favorite: {e}")
        drink = {"error": "An Error happened!!! Try Another Time!"}

    if drink and "error" not in drink:
        if Recipe.objects.filter(recipe_id=drink["idDrink"]).exists():
            logger.info(f"✓ Recipe {pk} already in database")
            recipe = get_object_or_404(Recipe, recipe_id=drink["idDrink"])
            if Favorite.objects.filter(user=request.user, recipe=recipe).exists():
                messages.warning(request, "You've already added this item before!")
                return redirect("myApp:my_favorites")

            Favorite.objects.create(user=request.user, recipe=recipe)
            messages.success(request, "Recipe has been added successfully")
            return redirect("myApp:my_favorites")

        else:
            logger.info(f"⚠️ Adding new recipe {pk} to database")
            # First we get or create the category
            drink_category, _ = Category.objects.get_or_create(
                name=drink["strCategory"]
            )

            # Then add the recipe
            recipe = Recipe.objects.create(
                recipe_id=drink["idDrink"],
                title=drink["strDrink"],
                instructions=drink["strInstructions"],
                category=drink_category,
                picture_url=drink["strDrinkThumb"],
            )

            # then the ingredients and amounts through RecipeIngredient Model
            ingredients_list = []
            amounts_list = []
            order_list = []
            for number in range(1, 16):
                if (
                    drink[f"strMeasure{number}"] is None
                    or drink[f"strIngredient{number}"] is None
                ):
                    break
                else:
                    ingredients_list.append(drink[f"strIngredient{number}"])
                    amounts_list.append(drink[f"strMeasure{number}"])
                    order_list.append(number)

            # Then we add them in the database through RecipeIngredient Model
            for index in range(len(amounts_list)):
                ingredient, _ = Ingredient.objects.get_or_create(
                    name=ingredients_list[index]
                )
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    ingredient=ingredient,
                    amount=amounts_list[index],
                    order=order_list[index],
                )

            # add it to user's favorite
            Favorite.objects.create(user=request.user, recipe=recipe)

    messages.success(request, "Recipe has been added successfully")
    return redirect("myApp:my_favorites")


def my_favorites(request):
    start = time.time()

    # UNOPTIMIZED: Without select_related
    with CaptureQueriesContext(connection) as ctx_bad:
        favorite_list_unoptimized = list(Favorite.objects.filter(user=request.user))
        # Accessing related objects will trigger additional queries
        for fav in favorite_list_unoptimized:
            _ = fav.recipe.title  # This triggers a query per favorite

    unoptimized_queries = len(ctx_bad)

    # OPTIMIZED: With select_related
    with CaptureQueriesContext(connection) as ctx_good:
        favorite_list = Favorite.objects.filter(user=request.user).select_related(
            "recipe"
        )
        favorite_list = list(favorite_list)

    optimized_queries = len(ctx_good)
    reduction = (
        ((unoptimized_queries - optimized_queries) / unoptimized_queries * 100)
        if unoptimized_queries > 0
        else 0
    )

    elapsed = (time.time() - start) * 1000

    logger.info(f"📊 MY FAVORITES QUERY OPTIMIZATION:")
    logger.info(f"   Unoptimized queries: {unoptimized_queries}")
    logger.info(f"   Optimized queries: {optimized_queries}")
    logger.info(f"   Reduction: {reduction:.1f}%")
    logger.info(f"   Load time: {elapsed:.2f}ms")

    print(f"\n📊 DATABASE OPTIMIZATION METRICS:")
    print(f"   Without select_related(): {unoptimized_queries} queries")
    print(f"   With select_related(): {optimized_queries} queries")
    print(f"   Improvement: {reduction:.1f}% fewer queries\n")

    return render(
        request,
        "myApp/my_favorites.html",
        {
            "favorites": favorite_list,
            "db_metrics": {
                "unoptimized": unoptimized_queries,
                "optimized": optimized_queries,
                "reduction_percent": reduction,
                "load_time_ms": elapsed,
            },
        },
    )


def delete_favorite(request, pk):
    favorite = get_object_or_404(Favorite, pk=pk)
    favorite.delete()
    messages.success(request, "Recipe has been deleted successfully")
    return redirect("myApp:my_favorites")


class RatingFormView(generic.FormView):
    model = Rating
    form_class = RatingForm
    success_url = reverse_lazy("my_favorites")
    template_name = "myApp/rating_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        context["recipe"] = get_object_or_404(Recipe, pk=pk)
        return context

    def form_valid(self, form):
        pk = self.kwargs.get("pk")
        recipe = get_object_or_404(Recipe, pk=pk)

        rating, created = Rating.objects.update_or_create(
            recipe=recipe,
            user=self.request.user,
            defaults={
                "rate": form.cleaned_data["rate"],
                "review": form.cleaned_data["review"],
            },
        )

        if created:
            logger.info(f"⭐ Rating added by {self.request.user} for recipe {pk}")
            messages.success(self.request, "Rating added successfully!")
        else:
            logger.info(f"⭐ Rating updated by {self.request.user} for recipe {pk}")
            messages.success(self.request, "Rating updated successfully!")

        return redirect("myApp:my_favorites")


class MyFavoriteDetailView(generic.DetailView):
    model = Favorite
    template_name = "myApp/detail_favorite.html"
