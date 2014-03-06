from django import http
from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect as django_redirect, render
from django.views.decorators.cache import cache_control, never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import basket
import commonware
from commonware.response.decorators import xframe_allow
from funfactory.urlresolvers import reverse
from tower import ugettext as _

from affiliates.facebook.auth import login
from affiliates.facebook.decorators import fb_login_required
from affiliates.facebook.forms import (BannerInstanceDeleteForm, FacebookAccountLinkForm,
                            FacebookBannerInstanceForm, LeaderboardFilterForm,
                            NewsletterSubscriptionForm)
from affiliates.facebook.tasks import add_click, generate_banner_instance_image
from affiliates.facebook.models import (FacebookAccountLink, FacebookBanner,
                             FacebookBannerInstance, FacebookClickStats,
                             FacebookUser)
from affiliates.facebook.utils import (decode_signed_request, fb_redirect, is_facebook_bot,
                            is_logged_in)
from affiliates.base.http import JSONResponse, JSONResponseBadRequest
from affiliates.base.utils import absolutify, redirect


SAFARI_WORKAROUND_KEY = 'safari_workaround'


log = commonware.log.getLogger('a.facebook')


@csrf_exempt
@xframe_allow
def load_app(request):
    """
    Create or authenticate the Facebook user and direct them to the correct
    area of the app upon their entry.
    """
    signed_request = request.POST.get('signed_request', None)
    if signed_request is None:
        # App wasn't loaded within a canvas, redirect to the home page.
        return redirect('base.landing')

    decoded_request = decode_signed_request(signed_request,
                                            settings.FACEBOOK_APP_SECRET)
    if decoded_request is None:
        return redirect('base.landing')

    # If user is using Safari, we need to apply the cookie workaround.
    useragent = request.META.get('HTTP_USER_AGENT', '')
    using_safari = 'Safari' in useragent and not 'Chrome' in useragent
    workaround_applied = SAFARI_WORKAROUND_KEY in request.COOKIES
    if using_safari and not workaround_applied:
        return fb_redirect(request,
                           absolutify(reverse('facebook.safari_workaround')),
                           top_window=True)

    user, created = (FacebookUser.objects.
            get_or_create_user_from_decoded_request(decoded_request))
    if user is None:
        # User has yet to authorize the app, redirect to the pre-auth promo.
        return fb_redirect(request,
                           absolutify(reverse('facebook.pre_auth_promo')))

    # Attach country data to the user object. This can only be retrieved from
    # the decoded request, so we add it here and login saves it.
    user.country = decoded_request['user'].get('country', user.country)

    # User has been authed, let's log them in.
    login(request, user)

    return fb_redirect(request, absolutify(reverse('facebook.banner_list')))


@xframe_allow
def pre_auth_promo(request):
    """Display an promotional message to users prompting them to authorize."""
    # Use the default locale if no banners are found in the requested locale.
    banners = FacebookBanner.objects.filter_by_locale(request.locale)[:6]
    if len(banners) == 0:
        banners = (FacebookBanner.objects
                   .filter_by_locale(settings.LANGUAGE_CODE))

    context = {
        'app_id': settings.FACEBOOK_APP_ID,
        'app_namespace': settings.FACEBOOK_APP_NAMESPACE,
        'app_permissions': settings.FACEBOOK_PERMISSIONS,
        'banners': banners
    }
    return render(request, 'facebook/pre_auth_promo.html', context)


@require_POST
@csrf_exempt
def deauthorize(request):
    """
    Callback that is pinged by Facebook when a user de-authorizes the app.

    Deletes the associated user and all their data. Returns a 400 if the signed
    request is missing or malformed, a 404 if the specified user could not be
    found, and a 200 if the removal was successful.
    """
    signed_request = request.POST.get('signed_request', None)
    if signed_request is None:
        return JSONResponseBadRequest({'error': 'No signed_request parameter '
                                                'found.'})

    decoded_request = decode_signed_request(signed_request,
                                            settings.FACEBOOK_APP_SECRET)
    if decoded_request is None or 'user_id' not in decoded_request:
        return JSONResponseBadRequest({'error': 'signed_request invalid.'})

    user = get_object_or_404(FacebookUser, id=decoded_request['user_id'])
    FacebookUser.objects.purge_user_data(user)
    return JSONResponse({'success': 'User data purged successfully.'})


@fb_login_required
@xframe_allow
def banner_create(request):
    form = FacebookBannerInstanceForm(request, request.POST or None)
    if request.method == 'POST':
        if not form.is_valid():
            return JSONResponse(form.errors, status=400)

        banner_instance = form.save()

        # The create form is submitted via an AJAX call. If the user wants to
        # include their profile picture on a banner, we return a 202 Accepted to
        # indicate we are processing the image. If they don't, we just return
        # a 201 Created to signify that the banner instance has been created
        # and it is safe to continue.
        if request.POST['next_action'] == 'share':
            next = absolutify(reverse('facebook.banners.share',
                                      args=[banner_instance.id]))
        else:
            next = absolutify(reverse('facebook.banner_list'))

        if form.cleaned_data['use_profile_image']:
            generate_banner_instance_image.delay(banner_instance.id)

            payload = {
                'check_url': reverse('facebook.banners.create_image_check',
                                     args=[banner_instance.id]),
                'next': next
            }
            return JSONResponse(payload, status=202)  # 202 Accepted
        else:
            # No processing needed.
            banner_instance.processed = True
            banner_instance.save()
            return JSONResponse({'next': next}, status=201)  # 201 Created

    return render(request, 'facebook/banner_create.html', {'form': form})


@fb_login_required
@never_cache
def banner_create_image_check(request, instance_id):
    """Check the status of generating a custom image for a banner instance."""
    banner_instance = get_object_or_404(FacebookBannerInstance, id=instance_id)
    return JSONResponse({'is_processed': banner_instance.processed})


@fb_login_required
@require_POST
@xframe_allow
def banner_delete(request):
    form = BannerInstanceDeleteForm(request.user, request.POST)
    if form.is_valid():
        banner_instance = form.cleaned_data['banner_instance']
        banner_instance.delete()
        messages.success(request, _('Your banner has been deleted.'))
    return banner_list(request)


@fb_login_required
@xframe_allow
def banner_list(request):
    # New users can't see this page.
    if request.user.is_new:
        return render(request, 'facebook/first_run.html')

    banner_instances = (request.user.banner_instance_set.filter(processed=True)
                        .select_related('banner'))
    return render(request, 'facebook/banner_list.html',
                        {'banner_instances': banner_instances})


@fb_login_required
@xframe_allow
def banner_share(request, instance_id):
    banner_instance = get_object_or_404(FacebookBannerInstance, id=instance_id,
                                        user=request.user)
    protocol = 'https' if request.is_secure() else 'http'
    next = absolutify(reverse('facebook.post_banner_share'),
                              protocol=protocol)
    return render(request, 'facebook/banner_share.html',
                        {'banner_instance': banner_instance, 'next': next})


def post_banner_share(request):
    """
    Redirect user back to the app after they've posted a banner to their feed.
    """
    if 'post_id' in request.GET:
        messages.success(request, _('You have successfully posted a banner to '
                                    'your wall !'))
    return django_redirect(settings.FACEBOOK_APP_URL)


@require_POST
def link_accounts(request):
    """
    Link the current user's account with an Affiliates account. Called via AJAX
    by the frontend.
    """
    if not is_logged_in(request):
        # Only logged in users can link accounts.
        return http.HttpResponseForbidden()

    form = FacebookAccountLinkForm(request.POST or None)
    if form.is_valid():
        affiliates_email = form.cleaned_data['affiliates_email']
        link = FacebookAccountLink.objects.create_link(request.user,
                                                       affiliates_email)
        if link:
            FacebookAccountLink.objects.send_activation_email(request, link)

    # Tell the user we were successful regardless of outcome in order to avoid
    # revealing valid emails.
    return http.HttpResponse()


def activate_link(request, activation_code):
    link = FacebookAccountLink.objects.activate_link(activation_code)
    if link:
        return django_redirect(settings.FACEBOOK_APP_URL)
    else:
        raise http.Http404


@fb_login_required
@xframe_allow
@require_POST
def remove_link(request):
    link = get_object_or_404(FacebookAccountLink, facebook_user=request.user)
    link.delete()
    return banner_list(request)


def follow_banner_link(request, banner_instance_id):
    """
    Add a click to a banner instance and redirect the user to the Firefox
    download page.
    """
    try:
        banner_instance = (FacebookBannerInstance.objects
                           .select_related('banner').get(id=banner_instance_id))
    except FacebookBannerInstance.DoesNotExist:
        return django_redirect(settings.FACEBOOK_DOWNLOAD_URL)

    # Do not add a click if the request is from the Facebook bot.
    if not is_facebook_bot(request):
        add_click.delay(banner_instance_id)

    return django_redirect(banner_instance.banner.link)


@fb_login_required
@xframe_allow
def leaderboard(request):
    form = LeaderboardFilterForm(request.GET or None)
    top_users = form.get_top_users()
    return render(request, 'facebook/leaderboard.html',
                        {'top_users': top_users, 'form': form})


@fb_login_required
@xframe_allow
def faq(request):
    return render(request, 'facebook/faq.html')


@fb_login_required
@xframe_allow
def invite(request):
    protocol = 'https' if request.is_secure() else 'http'
    next = absolutify(reverse('facebook.post_invite'), protocol=protocol)
    return render(request, 'facebook/invite.html', {'next': next})


def post_invite(request):
    """
    Redirect user back to the app after they've invited friends to download
    Firefox.
    """
    if request.GET.get('success', None):
        messages.success(request, _('You have successfully sent a message to one of your '
                                    'friends!'))
    return django_redirect(settings.FACEBOOK_APP_URL)


@fb_login_required
@require_POST
def newsletter_subscribe(request):
    form = NewsletterSubscriptionForm(request.user, request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            basket.subscribe(data['email'], settings.FACEBOOK_MAILING_LIST,
                             format=data['format'], country=data['country'],
                             source_url=request.build_absolute_uri())
        except basket.BasketException, e:
            log.error('Error subscribing email %s to mailing list: %s' %
                      (data['email'], e))

    # TODO: Send an error code if there was an error.
    return JSONResponse({'success': 'success'})


@fb_login_required
@cache_control(must_revalidate=True, max_age=3600)
def stats(request, year, month):
    """
    Returns statistics for the sidebar statistics display. Called via AJAX.
    """
    # Check for placeholder values and return a 400 if they are present.
    if month == ':month:' or year == ':year:':
        return JSONResponseBadRequest({'error': 'Invalid year/month value.'})

    clicks = FacebookClickStats.objects.total_for_month(request.user, year,
                                                        month)
    return JSONResponse({'clicks': clicks})


def safari_workaround(request):
    """
    Safari does not allow third-party requests to set cookies, but we need to
    set session and other cookies when users view the app through the Facebook
    iframe.

    To work around this, we send Safari users to this view, which sends a test
    cookie to the user. Because Safari allows third-party requests to set
    cookies if a cookie was sent with that request, the test cookie will allow
    us to set the session cookie normally.
    """
    response = django_redirect(settings.FACEBOOK_APP_URL)
    response.set_cookie(SAFARI_WORKAROUND_KEY, '1')
    return response
