import ssl
import requests
from cryptography import x509
from cryptography.x509.oid import ExtensionOID, AuthorityInformationAccessOID
from cryptography.hazmat.primitives.serialization import Encoding

pem = ssl.get_server_certificate(("www.nhg.at", 443))
leaf = x509.load_pem_x509_certificate(pem.encode())
aia = leaf.extensions.get_extension_for_oid(
    ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
url = next(d.access_location.value for d in aia
           if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS)
print("Download von:", url)

data = requests.get(url, timeout=20).content
try:
    inter = x509.load_der_x509_certificate(data)
except ValueError:
    inter = x509.load_pem_x509_certificate(data)

print("Aussteller:", inter.subject.rfc4514_string())
open("nhg-intermediate.pem", "wb").write(inter.public_bytes(Encoding.PEM))