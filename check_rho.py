from datetime import datetime 
res = msise_flat(datetime(2026,3,2,12,0,0), 400.0, lat=0, lon=0, f107=140, f107a=140, ap=15) 
print('res[5] raw =', res[5]) 
print('res[5] x1000 =', res[5]*1000) 
print('expected ~2e-12 kg/m3 at 400km Solar Mean') 
