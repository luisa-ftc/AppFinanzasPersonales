import pytest
from django.test import Client
from core.models import User, Account

@pytest.mark.django_db
def test_form_html_structure():
    user = User.objects.create_user(email='htmltest@x.com', username='htmltest', password='pass')
    client = Client()
    client.login(username='htmltest@x.com', password='pass')
    response = client.get('/accounts/new/')
    html = response.content.decode('utf-8')
    
    # Verificar estructura básica del formulario
    assert '<form method="post">' in html, 'form tag not found'
    assert 'type="submit"' in html, 'submit button not found'
    
    # Encontrar posición relativa de </form> y el botón submit
    form_end = html.find('</form>')
    submit_btn = html.find('type="submit"')
    
    print(f'submit button at char: {submit_btn}')
    print(f'</form> at char: {form_end}')
    print(f'Button is inside form: {submit_btn < form_end}')
    
    # Mostrar el trozo relevante del HTML
    start = max(0, submit_btn - 100)
    end = min(len(html), form_end + 50)
    print('--- Form end section ---')
    print(html[start:end])
    print('--- datalist in html ---')
    print('datalist present:', 'currency-datalist' in html)
    print('---')
    
    Account.objects.filter(user=user).delete()
    user.delete()
