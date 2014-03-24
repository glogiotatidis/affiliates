from django.conf import settings
from django.contrib.auth.models import User

from babel.core import Locale
from mock import patch
from nose.tools import eq_
from tower import activate

from affiliates.base.tests import TestCase
from affiliates.base.utils import absolutify, current_locale, redirect, get_object_or_none


class TestAbsolutify(TestCase):
    def setUp(self):
        self.patcher = patch.object(settings, 'SITE_URL', 'http://badge.mo.com')
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_basic(self):
        url = absolutify('/some/url')
        eq_(url, 'http://badge.mo.com/some/url')

    def test_protocol(self):
        url = absolutify('/some/url', protocol='https')
        eq_(url, 'https://badge.mo.com/some/url')

    def test_relative_protocol(self):
        """If protocol is a blank string, use a protocol-relative URL."""
        url = absolutify('/some/url', protocol='')
        eq_(url, '//badge.mo.com/some/url')

    def test_cdn(self):
        with patch.object(settings, 'CDN_DOMAIN', None):
            url = absolutify('/some/url', cdn=True)
            eq_(url, 'http://badge.mo.com/some/url')

        with patch.object(settings, 'CDN_DOMAIN', 'cdn.badge.mo.com'):
            url = absolutify('/some/url', cdn=True)
            eq_(url, 'http://cdn.badge.mo.com/some/url')


class TestRedirect(TestCase):
    urls = 'affiliates.base.tests.urls'

    def test_basic(self):
        with self.activate('en-US'):
            response = redirect('mock_view')
        eq_(response.status_code, 302)
        eq_(response['Location'], '/en-US/mock_view')

    def test_permanent(self):
        with self.activate('en-US'):
            response = redirect('mock_view', permanent=True)
        eq_(response.status_code, 301)
        eq_(response['Location'], '/en-US/mock_view')


class TestCurrentLocale(TestCase):
    def test_basic(self):
        """Test that the currently locale is correctly returned."""
        activate('fr')
        eq_(Locale('fr'), current_locale())

    def test_unknown(self):
        """
        Test that when the current locale is not supported by Babel, it
        defaults to en-US.
        """
        activate('fy')
        eq_(Locale('en', 'US'), current_locale())


class TestGetObjectOrNone(TestCase):
    def test_get(self):
        user = User.objects.create_user('get_object_or_none_test', 'a@b.com',
                                        None)
        eq_(get_object_or_none(User, username='get_object_or_none_test'), user)

    def test_none(self):
        eq_(get_object_or_none(User, username='does.not.exist'), None)
